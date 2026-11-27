"""Loads and validates the configuration files of spec section 6, with Pydantic.

Two files live in the config folder (``config/`` under the project root, or the folder named by
the environment variable ``DRS_CONFIG_DIR``):

* ``database.toml`` - which engine holds the DRS database and how to reach it;
* ``app.toml`` - everything else.

The models mirror the TOML layout (``[auth.local]`` is ``settings.auth.local``). Unknown keys are
rejected so a typo does not pass silently. A missing or invalid file stops the start-up with a
``ConfigError`` whose message says what to do.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator



def _project_root() -> Path:
    """The DRS folder: config/, runtime/ and designs/ live under it. The environment variable
    DRS_HOME names it; otherwise the source checkout the package runs from (``pip install -e .``),
    or the current folder for a package installed into site-packages."""
    env = os.environ.get("DRS_HOME")
    if env:
        return Path(env).resolve()
    checkout = Path(__file__).resolve().parent.parent
    return checkout if (checkout / "pyproject.toml").is_file() else Path.cwd().resolve()


PROJECT_ROOT = _project_root()

ENGINES = ("sqlite", "postgresql")

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,62}$")]
Positive = Annotated[int, Field(gt=0)]
NonNegative = Annotated[int, Field(ge=0)]


class ConfigError(Exception):
    """The configuration cannot be used. The message is meant for the person starting DRS."""


def config_dir() -> Path:
    env = os.environ.get("DRS_CONFIG_DIR")
    return Path(env).resolve() if env else PROJECT_ROOT / "config"


def resolve_path(value: str | Path) -> Path:
    """A relative path in a config file is relative to the project root."""
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------------------------
# database.toml

class SqliteSettings(_Section):
    path: Path = Path("runtime/drs.sqlite3")
    # The BI datasets, in a file (and a folder) of their own: a BI tool is given that file to read
    # and never the DRS database, which holds the password hashes and the stored secrets.
    bi_path: Path = Path("runtime/bi/datasets.sqlite3")
    # The mirrored source tables ([mirror]), in a file of their own: reports read it through a
    # datasource of type sqlite, so it holds nothing of the DRS database.
    mirror_path: Path = Path("runtime/mirror/mirror.sqlite3")

    @field_validator("path", "bi_path", "mirror_path")
    @classmethod
    def _absolute(cls, value: Path) -> Path:
        return resolve_path(value)


class PostgresSettings(_Section):
    host: str = "127.0.0.1"
    port: Annotated[int, Field(gt=0, lt=65536)] = 5432
    database: str = "drs"
    user: str = "drs_app"
    password_env: str = "DRS_DB_PASSWORD"
    schema_: Identifier = Field("drs", alias="schema")
    bi_schema: Identifier = "drs_bi"
    mirror_schema: Identifier = "drs_mirror"  # the mirrored source tables ([mirror])
    sslmode: Literal["disable", "allow", "prefer", "require", "verify-ca", "verify-full"] = "prefer"
    pool_size: Positive = 5
    max_overflow: NonNegative = 5

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    @property
    def schema(self) -> str:  # noqa: D401 - "schema" shadows a BaseModel attribute name
        return self.schema_

    def password(self) -> str | None:
        return os.environ.get(self.password_env)


class DatabaseSettings(_Section):
    active: Literal["sqlite", "postgresql"]
    sqlite: SqliteSettings = SqliteSettings()
    postgresql: PostgresSettings = PostgresSettings()

    def describe(self) -> str:
        """Engine and database, for the start-up log. Never contains the password."""
        if self.active == "sqlite":
            return f"sqlite ({self.sqlite.path}; BI datasets in {self.sqlite.bi_path})"
        pg = self.postgresql
        return f"postgresql ({pg.user}@{pg.host}:{pg.port}/{pg.database}, schema {pg.schema})"


# --------------------------------------------------------------------------------------------
# app.toml

class AppSection(_Section):
    name: str = "DRS"
    base_url: str = "http://localhost:8080"
    # Other addresses the portal is opened at, such as a domain served over https by a reverse
    # proxy (``siteurl``). A page is built for the address it was asked at.
    other_urls: list[str] = []
    host: str = "127.0.0.1"
    port: Annotated[int, Field(gt=0, lt=65536)] = 8080
    default_locale: Literal["vi", "en"] = "vi"
    timezone: str = "Asia/Ho_Chi_Minh"
    secret_key_env: str = "DRS_SECRET_KEY"
    workers: Annotated[int, Field(gt=0, le=64)] = 1  # web server processes (`serve`)
    forwarded_allow_ips: str = "127.0.0.1"  # reverse proxies trusted for X-Forwarded-For / -Proto
    auto_migrate: bool = True  # `serve` creates / migrates the DRS database before it starts

    @field_validator("base_url")
    @classmethod
    def _base_url(cls, value: str) -> str:
        from drscore.siteurl import check_origin
        return check_origin(value, "[app] base_url")

    @field_validator("other_urls")
    @classmethod
    def _other_urls(cls, value: list[str]) -> list[str]:
        from drscore.siteurl import check_origin
        return [check_origin(v, "[app] other_urls") for v in value]

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown time zone {value!r}") from exc
        return value


class SessionSection(_Section):
    idle_minutes: Positive = 60
    absolute_hours: Positive = 12
    cookie_secure: bool = False


class LocalAuthSection(_Section):
    enabled: bool = True
    min_password_length: Positive = 10
    max_failed_logins: Positive = 5
    lockout_minutes: Positive = 15


class SsoSection(_Section):
    enabled: bool = False
    issuer: str = ""
    client_id: str = ""
    client_secret_env: str = "DRS_SSO_CLIENT_SECRET"
    scopes: tuple[str, ...] = ("openid", "profile", "email")
    match_claim: str = "preferred_username"
    auto_provision: bool = False


class AuthSection(_Section):
    local: LocalAuthSection = LocalAuthSection()
    sso: SsoSection = SsoSection()


class DesignsSection(_Section):
    root: str = "designs"

    @property
    def root_path(self) -> Path:
        return resolve_path(self.root)


class CacheSection(_Section):
    lock_wait_seconds: Positive = 180
    lock_stale_seconds: Positive = 900


class LimitsSection(_Section):
    default_max_rows: Positive = 50000
    hard_max_rows: Positive = 500000
    default_timeout_seconds: Positive = 120
    hard_timeout_seconds: Positive = 900
    max_snapshot_mb: Positive = 200


class ExportSection(_Section):
    header_source: Literal["label", "field"] = "label"
    csv_delimiter: Annotated[str, Field(min_length=1, max_length=1)] = ","
    csv_bom: bool = True
    txt_delimiter: Annotated[str, Field(min_length=1)] = "|"
    txt_delimiter_replacement: str = "¦"
    txt_line_ending: Literal["crlf", "lf"] = "crlf"
    formula_guard: bool = True


class SupersetSection(_Section):
    enabled: bool = False
    base_url: str = "http://localhost:8088"
    # Superset's address for the browser on a page of another portal address ([app] other_urls):
    # {"https://portal.example.com" = "https://bi.example.com"}. Other portal addresses use base_url.
    site_urls: dict[str, str] = {}
    api_url: str = "http://localhost:8088"
    username: str = "drs_service"
    password_env: str = "DRS_SUPERSET_PASSWORD"
    guest_token_ttl_seconds: Positive = 300
    check_design: bool = True
    self_service: bool = False
    admin_username: str = ""
    admin_password_env: str = "DRS_SUPERSET_ADMIN_PASSWORD"
    database_name: str = "DRS"
    design_role_prefix: str = "DRS_DESIGN_"

    @field_validator("site_urls")
    @classmethod
    def _site_urls(cls, value: dict[str, str]) -> dict[str, str]:
        from drscore.siteurl import check_origin
        return {check_origin(k, "[bi.superset] site_urls"): check_origin(v, "[bi.superset] site_urls")
                for k, v in value.items()}

    @property
    def is_self_service(self) -> bool:
        return bool(self.enabled and self.self_service and self.admin_username)

    def admin_password(self) -> str | None:
        return os.environ.get(self.admin_password_env)


class BiSection(_Section):
    superset: SupersetSection = SupersetSection()


class MirrorSection(_Section):
    """Copies of source tables in the DRS database (drscore.mirror), so heavy report queries run there
    and not on the production source."""

    enabled: bool = False  # `serve` runs the mirrors that are due, in the background
    poll_seconds: Annotated[int, Field(ge=10)] = 60  # how often the server looks for a due mirror
    batch_rows: Annotated[int, Field(ge=100, le=1_000_000)] = 10000  # rows read and written at a time
    timeout_seconds: Positive = 3600  # the longest one source query of a mirror may take
    lock_minutes: Positive = 15  # a run's claim, renewed every minute while it copies: a stopped server frees it
    # PostgreSQL: the role given SELECT on the mirror schema and its tables - the login of the
    # datasource that reports read the mirror through. Empty: no grant is made.
    read_role: str = ""
    # PostgreSQL: the copies are UNLOGGED tables. A full copy deletes and inserts every row of its
    # source, and every one of those rows went through the WAL - archived and backed up with the
    # whole cluster (2026-10-28: the mirrors were ~70-98% of the DRS server's WAL, ~22 GB a day).
    # A copy can always be read again from its source, so it need not survive a crash: PostgreSQL
    # empties an unlogged table after one, and the next run of each mirror fills it again.
    # false: ordinary (logged) tables; existing copies are switched either way at their next run.
    unlogged: bool = True


class ScheduleSection(_Section):
    """Reports read again on a schedule (drscore.reports.schedule): ``python -m drscore report schedule``."""

    enabled: bool = False  # `serve` runs the report schedules that are due, in the background
    poll_seconds: Annotated[int, Field(ge=10)] = 60  # how often the server looks for a due schedule
    lock_minutes: Positive = 60  # a run's claim; longer than the slowest scheduled report


class PublicSection(_Section):
    """Public links (drscore.publiclink): a report opened by a secret URL, without signing in."""

    enabled: bool = False  # off: every /p/ address answers "not found"
    theme: Literal["dark", "light"] = "dark"  # of the page around the report
    locale: Literal["", "vi", "en"] = ""  # the page's language; empty: the reader's, else [app] default_locale
    # The account links run as by default (1.7.2): made at start-up when missing - no password, not an
    # administrator, it can never sign in. The Public links page grants it each report a link is made for.
    # Empty: none is made, every link names its own account.
    default_user: str = "user_public"


class ActivitySection(_Section):
    """The activity log (drscore.activity): every request a user makes, with its outcome."""

    enabled: bool = True
    page_opens: bool = True  # GET of a portal or admin page, besides every change and every refused request
    max_field_chars: Annotated[int, Field(ge=50, le=100_000)] = 4000  # one form / JSON value, longer ones cut


class FeedbackSection(_Section):
    """Góp ý (drscore.feedback): a signed-in user writes to the administrators."""

    enabled: bool = True
    files_dir: str = "runtime/feedback"  # the files sent with a feedback (screenshots, ...)
    max_files: Annotated[int, Field(ge=0, le=20)] = 5
    max_file_mb: Annotated[int, Field(ge=1, le=50)] = 8

    @property
    def files_path(self) -> Path:
        return resolve_path(self.files_dir)


class AiSection(_Section):
    """The AI agent (drscore.typhon). Off until enabled and given a key: then the portal shows it."""

    enabled: bool = False
    name: Annotated[str, Field(min_length=1, max_length=30)] = "Typhon"
    # The Anthropic API key: this environment variable, else the encrypted secret `ai.api_key`
    # (`python -m drscore secret set ai.api_key`). Never in a file in clear.
    api_key_env: str = "DRS_AI_API_KEY"
    base_url: str = ""  # empty: the Anthropic API; else a relay of it
    model: str = "claude-opus-5-5"
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    max_tokens: Annotated[int, Field(ge=256, le=64000)] = 8000
    timeout_seconds: Annotated[int, Field(ge=10, le=900)] = 180
    max_tool_rounds: Annotated[int, Field(ge=1, le=20)] = 8
    # Who may ask: these accounts (usernames) and the members of these roles (role codes); administrators
    # always. Both empty: every signed-in user.
    allowed_users: list[str] = []
    allowed_roles: list[str] = []
    # May Typhon run a report for the asker and read its rows (with the asker's rights and row filters)?
    read_report_data: bool = False
    max_rows_to_model: Annotated[int, Field(ge=1, le=5000)] = 200
    # A report Typhon runs stops at this many rows (some reach hundreds of thousands): Typhon is then told to
    # narrow the parameters. A result already in the cache is read, and only max_rows_to_model rows go on.
    max_report_rows: Annotated[int, Field(ge=1, le=1_000_000)] = 10000
    questions_per_user_per_day: Annotated[int, Field(ge=0)] = 50  # 0: no limit
    monthly_budget_usd: Annotated[float, Field(ge=0)] = 20.0  # 0: no limit
    # USD per million tokens [input, output], for the cost kept with each question and the budget.
    prices: dict[str, tuple[float, float]] = {
        "claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0), "claude-haiku-5-5": (0.1, 0.5),
    }
    # A new feedback is read by Typhon first: an answer to the sender, a note for the administrators.
    feedback_intake: bool = True


class TelegramSection(_Section):
    chat_id: str = ""  # the group's id (-100...); empty: Telegram is off
    # The bot token: this environment variable, else the encrypted secret `telegram.bot_token`.
    bot_token_env: str = "DRS_TELEGRAM_BOT_TOKEN"
    api_url: str = "https://api.telegram.org"


class ZaloSection(_Section):
    chat_id: str = ""  # the group's id; empty: Zalo is off
    bot_token_env: str = "DRS_ZALO_BOT_TOKEN"  # else the encrypted secret `zalo.bot_token`
    api_url: str = "https://bot-api.zaloplatforms.com"


NOTICE_EVENTS = ("feedback_new", "feedback_comment", "feedback_reopened", "design_submitted", "ai_budget")


class NotifySection(_Section):
    """Notices to a Telegram group and a Zalo group (drscore.notify). A channel sends only when both its
    group (chat_id) and its bot token are set; until then nothing is sent and nothing fails."""

    events: list[Literal[NOTICE_EVENTS]] = list(NOTICE_EVENTS)  # type: ignore[valid-type]
    prefix: str = ""  # put before every notice, e.g. "[TEST] " on a test installation
    timeout_seconds: Annotated[int, Field(ge=1, le=60)] = 10
    telegram: TelegramSection = TelegramSection()
    zalo: ZaloSection = ZaloSection()


class AppSettings(_Section):
    app: AppSection = AppSection()
    session: SessionSection = SessionSection()
    auth: AuthSection = AuthSection()
    designs: DesignsSection = DesignsSection()
    cache: CacheSection = CacheSection()
    limits: LimitsSection = LimitsSection()
    export: ExportSection = ExportSection()
    bi: BiSection = BiSection()
    mirror: MirrorSection = MirrorSection()
    schedule: ScheduleSection = ScheduleSection()
    public: PublicSection = PublicSection()
    activity: ActivitySection = ActivitySection()
    feedback: FeedbackSection = FeedbackSection()
    ai: AiSection = AiSection()
    notify: NotifySection = NotifySection()

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.app.timezone)


class Settings(_Section):
    database: DatabaseSettings
    app: AppSettings
    config_dir: Path


# --------------------------------------------------------------------------------------------
# Loading

def _read_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        example = path.with_name(path.stem + ".example.toml")
        raise ConfigError(
            f"Configuration file not found: {path}. "
            f"Copy {example.name} to {path.name} in {path.parent} and edit it."
        )
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path.name} is not valid TOML: {exc}") from exc


def _explain(file: str, exc: ValidationError) -> ConfigError:
    lines = []
    for err in exc.errors():
        where = ".".join(str(p) for p in err["loc"])
        lines.append(f"{where}: {err['msg']}")
    return ConfigError(f"{file}: " + "; ".join(lines))


def load_database_settings(directory: Path | None = None) -> DatabaseSettings:
    data = _read_toml((directory or config_dir()) / "database.toml")
    active = os.environ.get("DRS_DB_ACTIVE") or data.get("active")
    if active not in ENGINES:
        raise ConfigError(
            f"database.toml: active = {active!r} is not allowed. "
            f'Use "sqlite" or "postgresql".'
        )
    try:
        return DatabaseSettings.model_validate({**data, "active": active})
    except ValidationError as exc:
        raise _explain("database.toml", exc) from exc


def load_app_settings(directory: Path | None = None) -> AppSettings:
    data = _read_toml((directory or config_dir()) / "app.toml")
    try:
        return AppSettings.model_validate(data)
    except ValidationError as exc:
        raise _explain("app.toml", exc) from exc


def load_settings(directory: Path | None = None) -> Settings:
    directory = directory or config_dir()
    return Settings(
        database=load_database_settings(directory),
        app=load_app_settings(directory),
        config_dir=directory,
    )


_current: Settings | None = None


def get_settings() -> Settings:
    """The settings of this process, loaded once."""
    global _current
    if _current is None:
        _current = load_settings()
    return _current


def reset_settings() -> None:
    """Forgets the loaded settings (tests, or a CLI command that changes the environment)."""
    global _current
    _current = None
