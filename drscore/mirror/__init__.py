"""Mirrors: copies of source tables in the DRS database, refreshed on a schedule (docs/mirror.md).

A report that reads millions of rows of a production database - an ERP - loads that server every
time it runs. A mirror copies the source tables the report needs, one table at a time, into the
mirror schema of the DRS database (``[postgresql] mirror_schema``; on SQLite the file ``[sqlite]
mirror_path``); the report is then written against the copy, through an ordinary datasource, and
the heavy joins and aggregations run there. The source only answers plain reads of one table:

* **full**: every row (that passes the mirror's filter) is read again and replaces the copy;
* **incremental**: only the rows whose watermark column (a modification time, a growing id) is
  above the last value read; they are inserted or updated by the row key. A periodic full copy
  (``full_refresh_hours``) removes the rows deleted at the source.

Several mirrors may write one target table - the same table of several servers: each row carries
the code of the datasource it came from (``_source``), and a mirror replaces only its own rows.
"""

from drscore.mirror.spec import MirrorError, MirrorSpec  # noqa: F401
