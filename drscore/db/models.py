"""The tables of the DRS database (spec section 8.2).

Models carry no schema. On PostgreSQL the connection's ``search_path`` puts the configured schema
(default ``drs``) first, so the same models, the same migrations and hand-written SQL all address
the same tables on both engines.

Every default is also a server default, so a row inserted by hand may leave those columns out
(spec section 8.1, rule 10).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Text,
    false,
    func,
    text,
    true,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from drscore.db.types import IdType, JsonType, UTCDateTime, utcnow

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# sqlite_autoincrement: an id is never reused after a delete (spec section 8.1, rule 3).
AUTOINC = {"sqlite_autoincrement": True}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _id() -> Mapped[int]:
    return mapped_column(IdType, primary_key=True, autoincrement=True)


def _fk(target: str, *, nullable: bool = False, cascade: bool = True) -> Mapped[Any]:
    return mapped_column(
        IdType,
        ForeignKey(target, ondelete="CASCADE" if cascade else None),
        nullable=nullable,
    )


def _bool(default: bool) -> Mapped[bool]:
    return mapped_column(
        Boolean(create_constraint=False),
        nullable=False,
        default=default,
        server_default=true() if default else false(),
    )


def _int(default: int) -> Mapped[int]:
    return mapped_column(Integer, nullable=False, default=default, server_default=text(str(default)))


def _order() -> Mapped[Decimal]:
    """A decimal display order, so an item can be put between two others (1.5 between 1 and 2)."""
    return mapped_column(Numeric(10, 2), nullable=False, default=Decimal(0), server_default=text("0"))


def _now() -> Mapped[datetime]:
    return mapped_column(UTCDateTime(), nullable=False, default=utcnow, server_default=func.current_timestamp())


def _now_updated() -> Mapped[datetime]:
    return mapped_column(
        UTCDateTime(), nullable=False, default=utcnow, onupdate=utcnow,
        server_default=func.current_timestamp(),
    )


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


# --------------------------------------------------------------------------------------------
# Users and roles

class User(Base):
    __tablename__ = "drs_user"
    __table_args__ = AUTOINC

    user_id: Mapped[int] = _id()
    username: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str | None] = mapped_column(String(200))
    password_hash: Mapped[str | None] = mapped_column(String(255))
    sso_subject: Mapped[str | None] = mapped_column(String(255), unique=True)
    is_admin: Mapped[bool] = _bool(False)
    is_active: Mapped[bool] = _bool(True)
    # Set while the password is one everybody knows (the default administrator's): the session
    # then opens nothing but the account page, until the user has chosen another one.
    must_change_password: Mapped[bool] = _bool(False)
    failed_login_count: Mapped[int] = _int(0)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now_updated()

    roles: Mapped[list[Role]] = relationship(secondary="drs_user_role", back_populates="users")
    attributes: Mapped[list[UserAttribute]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )

    def __str__(self) -> str:
        return self.username


class Role(Base):
    __tablename__ = "drs_role"
    __table_args__ = AUTOINC

    role_id: Mapped[int] = _id()
    role_code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    role_name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = _bool(True)

    users: Mapped[list[User]] = relationship(secondary="drs_user_role", back_populates="roles")

    def __str__(self) -> str:
        return self.role_code


class UserRole(Base):
    __tablename__ = "drs_user_role"
    __table_args__ = (PrimaryKeyConstraint("user_id", "role_id"),)

    user_id: Mapped[int] = _fk("drs_user.user_id")
    role_id: Mapped[int] = _fk("drs_role.role_id")


class UserAttribute(Base):
    __tablename__ = "drs_user_attribute"
    __table_args__ = (PrimaryKeyConstraint("user_id", "attr_name", "attr_value"),)

    user_id: Mapped[int] = _fk("drs_user.user_id")
    attr_name: Mapped[str] = mapped_column(String(100), nullable=False)
    attr_value: Mapped[str] = mapped_column(String(200), nullable=False)

    user: Mapped[User] = relationship(back_populates="attributes")


# --------------------------------------------------------------------------------------------
# Datasources, groups, reports

DB_TYPES = ("mssql", "oracle", "postgresql", "sqlite")
AUTH_METHODS = ("password", "windows", "none")
SSL_MODES = ("disable", "prefer", "require", "verify")
ORACLE_CONNECT_BY = ("service_name", "sid")


class Datasource(Base):
    """Everything needed to connect to a source server, as a DBA tool (Navicat, DBeaver) keeps it.
    The password is never stored in clear (secret_ref)."""

    __tablename__ = "drs_datasource"
    __table_args__ = (
        CheckConstraint(_in("db_type", DB_TYPES), name="db_type"),
        CheckConstraint(_in("auth_method", AUTH_METHODS), name="auth_method"),
        CheckConstraint(f"ssl_mode IS NULL OR {_in('ssl_mode', SSL_MODES)}", name="ssl_mode"),
        CheckConstraint(f"oracle_connect_by IS NULL OR {_in('oracle_connect_by', ORACLE_CONNECT_BY)}",
                        name="oracle_connect_by"),
        AUTOINC,
    )

    datasource_id: Mapped[int] = _id()
    datasource_code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    datasource_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000))
    db_type: Mapped[str] = mapped_column(String(20), nullable=False)
    host: Mapped[str | None] = mapped_column(String(200))
    port: Mapped[int | None] = mapped_column(Integer)
    instance_name: Mapped[str | None] = mapped_column(String(100))  # SQL Server named instance
    database_name: Mapped[str] = mapped_column(String(500), nullable=False)  # Oracle: service name or SID
    oracle_connect_by: Mapped[str | None] = mapped_column(String(20))  # service_name (default) / sid
    default_schema: Mapped[str | None] = mapped_column(String(128))
    auth_method: Mapped[str] = mapped_column(String(10), nullable=False, default="password",
                                             server_default="password")
    username: Mapped[str | None] = mapped_column(String(200))
    secret_ref: Mapped[str | None] = mapped_column(String(1000))
    ssl_mode: Mapped[str | None] = mapped_column(String(10))  # NULL = the driver's default
    trust_server_certificate: Mapped[bool] = _bool(False)
    connect_timeout_seconds: Mapped[int | None] = mapped_column(Integer)
    driver: Mapped[str | None] = mapped_column(String(50))
    options_json: Mapped[Any | None] = mapped_column(JsonType)  # any other driver option
    is_active: Mapped[bool] = _bool(True)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now_updated()

    def __str__(self) -> str:
        return self.datasource_code


class ReportGroup(Base):
    __tablename__ = "drs_report_group"
    __table_args__ = AUTOINC

    group_id: Mapped[int] = _id()
    group_code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    group_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000))
    icon: Mapped[str | None] = mapped_column(String(50))
    sort_order: Mapped[int] = _int(0)
    is_active: Mapped[bool] = _bool(True)

    reports: Mapped[list[Report]] = relationship(back_populates="group", passive_deletes=True)

    def __str__(self) -> str:
        return f"{self.group_code} - {self.group_name}"


REPORT_TYPES = ("GRID", "HTML", "BI")


class Report(Base):
    __tablename__ = "drs_report"
    __table_args__ = (CheckConstraint(_in("report_type", REPORT_TYPES), name="report_type"), AUTOINC)

    report_id: Mapped[int] = _id()
    report_code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    report_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000))
    # No ON DELETE CASCADE: deleting a group that still holds reports is refused.
    group_id: Mapped[int] = _fk("drs_report_group.group_id", cascade=False)
    # The view the report page opens on: GRID, HTML or BI (owner decision 2026-10-06: every report
    # has a grid, and may also be viewed through an HTML design and a BI dashboard).
    report_type: Mapped[str] = mapped_column(String(10), nullable=False, default="GRID", server_default="GRID")
    datasource_id: Mapped[int | None] = _fk("drs_datasource.datasource_id", nullable=True, cascade=False)
    query_text: Mapped[str | None] = mapped_column(Text)
    # Where the designs are (spec section 13): NULL = that view is not designed.
    html_design_uri: Mapped[str | None] = mapped_column(String(1000))
    bi_design_uri: Mapped[str | None] = mapped_column(String(1000))
    # The grid view of the report; false hides the rows (and their export) behind a dashboard.
    grid_enabled: Mapped[bool] = _bool(True)
    bi_dataset_table: Mapped[str | None] = mapped_column(String(63))
    cache_ttl_seconds: Mapped[int] = _int(0)
    serve_stale_on_error: Mapped[bool] = _bool(False)
    timeout_seconds: Mapped[int | None] = mapped_column(Integer)
    max_rows: Mapped[int | None] = mapped_column(Integer)
    is_restricted: Mapped[bool] = _bool(False)
    sort_order: Mapped[Decimal] = _order()
    is_active: Mapped[bool] = _bool(True)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now_updated()
    created_by: Mapped[str | None] = mapped_column(String(100))
    updated_by: Mapped[str | None] = mapped_column(String(100))

    group: Mapped[ReportGroup] = relationship(back_populates="reports")
    datasource: Mapped[Datasource | None] = relationship()

    def __str__(self) -> str:
        return self.report_code


PARAM_DATA_TYPES = ("text", "int", "decimal", "date", "datetime", "bool")
PARAM_INPUT_KINDS = ("input", "select", "multiselect")
MULTI_BIND_MODES = ("expand", "csv")


class ReportParam(Base):
    __tablename__ = "drs_report_param"
    __table_args__ = (
        CheckConstraint(_in("data_type", PARAM_DATA_TYPES), name="data_type"),
        CheckConstraint(_in("input_kind", PARAM_INPUT_KINDS), name="input_kind"),
        CheckConstraint(_in("multi_bind_mode", MULTI_BIND_MODES), name="multi_bind_mode"),
        Index(None, "report_id", "param_name", unique=True),
        AUTOINC,
    )

    param_id: Mapped[int] = _id()
    report_id: Mapped[int] = _fk("drs_report.report_id")
    param_name: Mapped[str] = mapped_column(String(50), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    data_type: Mapped[str] = mapped_column(String(10), nullable=False)
    input_kind: Mapped[str] = mapped_column(String(12), nullable=False, default="input", server_default="input")
    is_required: Mapped[bool] = _bool(False)
    default_value: Mapped[str | None] = mapped_column(String(500))
    options_json: Mapped[Any | None] = mapped_column(JsonType)
    options_query: Mapped[str | None] = mapped_column(Text)
    options_datasource_id: Mapped[int | None] = _fk("drs_datasource.datasource_id", nullable=True, cascade=False)
    multi_bind_mode: Mapped[str] = mapped_column(String(10), nullable=False, default="expand", server_default="expand")
    min_value: Mapped[str | None] = mapped_column(String(50))
    max_value: Mapped[str | None] = mapped_column(String(50))
    max_length: Mapped[int | None] = mapped_column(Integer)
    regex: Mapped[str | None] = mapped_column(String(500))
    sort_order: Mapped[Decimal] = _order()
    is_active: Mapped[bool] = _bool(True)  # an inactive parameter is not asked for nor bound

    report: Mapped[Report] = relationship()
    options_datasource: Mapped[Datasource | None] = relationship()


COLUMN_DATA_TYPES = ("text", "int", "decimal", "float", "bool", "date", "datetime", "time")
ALIGNS = ("left", "center", "right")
AGGREGATES = ("sum", "avg", "count", "min", "max")


class ReportColumn(Base):
    __tablename__ = "drs_report_column"
    __table_args__ = (
        CheckConstraint(f"data_type IS NULL OR {_in('data_type', COLUMN_DATA_TYPES)}", name="data_type"),
        CheckConstraint(f"align IS NULL OR {_in('align', ALIGNS)}", name="align"),
        CheckConstraint(f"footer_aggregate IS NULL OR {_in('footer_aggregate', AGGREGATES)}", name="footer_aggregate"),
        Index(None, "report_id", "field_name", unique=True),
        AUTOINC,
    )

    column_id: Mapped[int] = _id()
    report_id: Mapped[int] = _fk("drs_report.report_id")
    field_name: Mapped[str] = mapped_column(String(128), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    data_type: Mapped[str | None] = mapped_column(String(10))
    display_format: Mapped[str | None] = mapped_column(String(30))
    width_px: Mapped[int | None] = mapped_column(Integer)
    align: Mapped[str | None] = mapped_column(String(10))
    is_visible: Mapped[bool] = _bool(True)
    is_exported: Mapped[bool] = _bool(True)
    is_sortable: Mapped[bool] = _bool(True)
    is_filterable: Mapped[bool] = _bool(True)
    is_frozen: Mapped[bool] = _bool(False)
    footer_aggregate: Mapped[str | None] = mapped_column(String(10))
    sort_order: Mapped[int] = _int(0)

    report: Mapped[Report] = relationship()


class ReportRowFilter(Base):
    __tablename__ = "drs_report_row_filter"
    __table_args__ = (Index(None, "report_id", "column_name", unique=True), AUTOINC)

    row_filter_id: Mapped[int] = _id()
    report_id: Mapped[int] = _fk("drs_report.report_id")
    column_name: Mapped[str] = mapped_column(String(128), nullable=False)
    attr_name: Mapped[str] = mapped_column(String(100), nullable=False)

    report: Mapped[Report] = relationship()


# --------------------------------------------------------------------------------------------
# Grants

def _grant_args(table: str, object_column: str) -> tuple:
    return (
        CheckConstraint("(user_id IS NULL) <> (role_id IS NULL)", name="one_principal"),
        Index(
            f"uq_{table}_user", object_column, "user_id", unique=True,
            postgresql_where=text("user_id IS NOT NULL"), sqlite_where=text("user_id IS NOT NULL"),
        ),
        Index(
            f"uq_{table}_role", object_column, "role_id", unique=True,
            postgresql_where=text("role_id IS NOT NULL"), sqlite_where=text("role_id IS NOT NULL"),
        ),
        AUTOINC,
    )


class GrantGroup(Base):
    __tablename__ = "drs_grant_group"
    __table_args__ = _grant_args("drs_grant_group", "group_id")

    grant_id: Mapped[int] = _id()
    group_id: Mapped[int] = _fk("drs_report_group.group_id")
    user_id: Mapped[int | None] = _fk("drs_user.user_id", nullable=True)
    role_id: Mapped[int | None] = _fk("drs_role.role_id", nullable=True)
    can_export: Mapped[bool] = _bool(True)
    can_refresh: Mapped[bool] = _bool(False)
    granted_by: Mapped[str | None] = mapped_column(String(100))
    granted_at: Mapped[datetime] = _now()

    group: Mapped[ReportGroup] = relationship()
    user: Mapped[User | None] = relationship()
    role: Mapped[Role | None] = relationship()


class GrantReport(Base):
    __tablename__ = "drs_grant_report"
    __table_args__ = _grant_args("drs_grant_report", "report_id")

    grant_id: Mapped[int] = _id()
    report_id: Mapped[int] = _fk("drs_report.report_id")
    user_id: Mapped[int | None] = _fk("drs_user.user_id", nullable=True)
    role_id: Mapped[int | None] = _fk("drs_role.role_id", nullable=True)
    can_export: Mapped[bool] = _bool(True)
    can_refresh: Mapped[bool] = _bool(False)
    granted_by: Mapped[str | None] = mapped_column(String(100))
    granted_at: Mapped[datetime] = _now()

    report: Mapped[Report] = relationship()
    user: Mapped[User | None] = relationship()
    role: Mapped[Role | None] = relationship()


# --------------------------------------------------------------------------------------------
# Cache

class ReportSnapshot(Base):
    __tablename__ = "drs_report_snapshot"
    __table_args__ = (
        Index("ix_drs_report_snapshot_lookup", "report_id", "cache_key", text("created_at DESC")),
        Index(None, "expires_at"),
        AUTOINC,
    )

    snapshot_id: Mapped[int] = _id()
    report_id: Mapped[int] = _fk("drs_report.report_id")
    # Copied from the report so the history keeps it after the report is deleted.
    report_code: Mapped[str | None] = mapped_column(String(50))
    cache_key: Mapped[str] = mapped_column(String(64), nullable=False)
    params_json: Mapped[Any] = mapped_column(JsonType, nullable=False)
    columns_json: Mapped[Any] = mapped_column(JsonType, nullable=False)
    rows_json: Mapped[Any] = mapped_column(JsonType, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    byte_size: Mapped[int] = mapped_column(IdType, nullable=False)
    source_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(100))


ARCHIVE_REASONS = ("EXPIRED", "CLEARED")


class ReportSnapshotHistory(Base):
    """Snapshots moved out of drs_report_snapshot, for every report (owner decision 2026-10-06:
    cleaning the cache moves snapshots here, it never loses them).

    Filled by the application (``drscore.reports.cache``): the automatic housekeeping moves expired
    snapshots (``EXPIRED``), ``cache clear`` moves a report's snapshots (``CLEARED``). No foreign
    key: the history outlives its report.
    """

    __tablename__ = "drs_report_snapshot_history"
    __table_args__ = (
        CheckConstraint(_in("archive_reason", ARCHIVE_REASONS), name="archive_reason"),
        Index(None, "report_id", "cache_key"),
        Index(None, "report_code"),
        Index(None, "archived_at"),
    )

    snapshot_id: Mapped[int] = mapped_column(IdType, primary_key=True, autoincrement=False)
    report_id: Mapped[int] = mapped_column(IdType, nullable=False)
    report_code: Mapped[str | None] = mapped_column(String(50))
    cache_key: Mapped[str] = mapped_column(String(64), nullable=False)
    params_json: Mapped[Any] = mapped_column(JsonType, nullable=False)
    columns_json: Mapped[Any] = mapped_column(JsonType, nullable=False)
    rows_json: Mapped[Any] = mapped_column(JsonType, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    byte_size: Mapped[int] = mapped_column(IdType, nullable=False)
    source_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(100))
    archived_at: Mapped[datetime] = _now()
    archive_reason: Mapped[str] = mapped_column(String(10), nullable=False)


class ReportLock(Base):
    __tablename__ = "drs_report_lock"
    __table_args__ = (PrimaryKeyConstraint("report_id", "cache_key"),)

    report_id: Mapped[int] = _fk("drs_report.report_id")
    cache_key: Mapped[str] = mapped_column(String(64), nullable=False)
    locked_by: Mapped[str] = mapped_column(String(100), nullable=False)
    locked_at: Mapped[datetime] = _now()


class BiDataset(Base):
    """Which snapshot of a BI report is loaded in its dataset table (spec section 14.3)."""

    __tablename__ = "drs_bi_dataset"

    report_id: Mapped[int] = mapped_column(IdType, ForeignKey("drs_report.report_id", ondelete="CASCADE"),
                                           primary_key=True)
    table_name: Mapped[str] = mapped_column(String(128), nullable=False)  # as Superset sees it
    snapshot_id: Mapped[int] = mapped_column(IdType, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    loaded_at: Mapped[datetime] = _now()


# --------------------------------------------------------------------------------------------
# Sessions and logs

class Session(Base):
    __tablename__ = "drs_session"
    __table_args__ = (CheckConstraint(_in("auth_method", ("local", "sso")), name="auth_method"),)

    session_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = _fk("drs_user.user_id")
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)
    auth_method: Mapped[str] = mapped_column(String(10), nullable=False)
    created_at: Mapped[datetime] = _now()
    last_seen_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    client_ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(400))


# OPEN: the report page was opened; RUN: the source was executed; VIEW: a result was shown from a
# snapshot (cache hit, another tab, the HTML frame, a dashboard).
ACCESS_ACTIONS = ("LOGIN", "LOGOUT", "OPEN", "VIEW", "RUN", "EXPORT_XLSX", "EXPORT_CSV", "EXPORT_TXT", "BI_TOKEN")
ACCESS_STATUSES = ("OK", "DENIED", "ERROR")


class AccessLog(Base):
    __tablename__ = "drs_access_log"
    __table_args__ = (
        CheckConstraint(_in("action", ACCESS_ACTIONS), name="action"),
        CheckConstraint(_in("status", ACCESS_STATUSES), name="status"),
        Index(None, "logged_at"),
        AUTOINC,
    )

    log_id: Mapped[int] = _id()
    logged_at: Mapped[datetime] = _now()
    username: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    report_code: Mapped[str | None] = mapped_column(String(50))
    params_json: Mapped[Any | None] = mapped_column(JsonType)
    snapshot_id: Mapped[int | None] = mapped_column(IdType)  # no FK: the log outlives snapshots
    cache_hit: Mapped[bool | None] = mapped_column(Boolean(create_constraint=False))
    row_count: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(40))
    error_message: Mapped[str | None] = mapped_column(String(2000))
    client_ip: Mapped[str | None] = mapped_column(String(64))
    view_key: Mapped[str | None] = mapped_column(String(10))  # grid / html / bi
    user_agent: Mapped[str | None] = mapped_column(String(400))
    request_id: Mapped[str | None] = mapped_column(String(32))  # the same id as in runtime/logs/drs.log


class AuditLog(Base):
    __tablename__ = "drs_audit_log"
    __table_args__ = (Index(None, "logged_at"), AUTOINC)

    audit_id: Mapped[int] = _id()
    logged_at: Mapped[datetime] = _now()
    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    object_type: Mapped[str] = mapped_column(String(40), nullable=False)
    object_key: Mapped[str] = mapped_column(String(200), nullable=False)
    before_json: Mapped[Any | None] = mapped_column(JsonType)
    after_json: Mapped[Any | None] = mapped_column(JsonType)


ALL_TABLES = sorted(Base.metadata.tables)
