"""The background thread of ``serve`` that runs the mirrors that are due ([mirror] enabled).

Every server process (each worker of ``serve``) has one; a mirror is claimed in the DRS database
before it runs (``runner.claim``), so two processes - or two servers on one database - never copy
the same table at once. Mirrors run one after the other in a thread: a long copy holds no web
request.
"""

from __future__ import annotations

import logging
import threading

from drscore.db.engine import Database
from drscore.mirror import runner
from drscore.settings import AppSettings

log = logging.getLogger(__name__)


class MirrorScheduler:
    def __init__(self, database: Database, settings: AppSettings):
        self.database = database
        self.settings = settings
        self.owner = runner.owner_name()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="drs-mirror", daemon=True)
            self._thread.start()
            log.info("mirror scheduler started (every %d s)", self.settings.mirror.poll_seconds)

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        # The first look waits one interval: the server starts and serves before it copies anything.
        while not self._stop.wait(self.settings.mirror.poll_seconds):
            try:
                runner.run_due(self.database, self.settings, owner=self.owner)
            except Exception:  # the DRS database unreachable for a moment: try again later
                log.exception("mirror scheduler: the due mirrors could not be run")
