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
    UniqueConstraint,
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
    # The user's Superset account when its name is not the DRS user name (Superset has its own
    # sign-in): the BI page finds "Mine" by it. Empty: the account with the DRS user name.
    superset_username: Mapped[str | None] = mapped_column(String(100), unique=True, index=True)
    is_admin: Mapped[bool] = _bool(False)
    is_active: Mapped[bool] = _bool(True)
    # Set while the password is one everybody knows (the default administrator's): the session
    # then opens nothing but the account page, until the user has chosen another one.
    must_change_password: Mapped[bool] = _bool(False)
    # Days one sign-in of this user lasts, used or not, instead of [session] idle_minutes and
    # absolute_hours (owner, ORD 402: the viewers of a dashboard stay signed in for a month, the
    # browser closed or not). Empty: the [session] limits.
    session_days: Mapped[int | None] = mapped_column(Integer)
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

    @property
    def superset_account(self) -> str:
        """The name of the user's Superset account: the one linked on the user, else the DRS name."""
        return self.superset_username or self.username


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
    can_design: Mapped[bool] = _bool(False)
    can_publish: Mapped[bool] = _bool(False)
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


class GrantDatasource(Base):
    __tablename__ = "drs_grant_datasource"
    __table_args__ = _grant_args("drs_grant_datasource", "datasource_id")

    grant_id: Mapped[int] = _id()
    datasource_id: Mapped[int] = _fk("drs_datasource.datasource_id")
    user_id: Mapped[int | None] = _fk("drs_user.user_id", nullable=True)
    role_id: Mapped[int | None] = _fk("drs_role.role_id", nullable=True)
    granted_by: Mapped[str | None] = mapped_column(String(100))
    granted_at: Mapped[datetime] = _now()

    datasource: Mapped[Datasource] = relationship()
    user: Mapped[User | None] = relationship()
    role: Mapped[Role | None] = relationship()


# --------------------------------------------------------------------------------------------
# Designer drafts

DRAFT_STATUSES = ("DRAFT", "PENDING", "APPROVED", "REJECTED")


class ReportDraft(Base):
    __tablename__ = "drs_report_draft"
    __table_args__ = (
        CheckConstraint(_in("status", DRAFT_STATUSES), name="status"),
        Index(None, "author"),
        Index(None, "status"),
        Index(
            "uq_drs_report_draft_open_code", "report_code", unique=True,
            postgresql_where=text("status IN ('DRAFT', 'PENDING')"),
            sqlite_where=text("status IN ('DRAFT', 'PENDING')"),
        ),
        AUTOINC,
    )

    draft_id: Mapped[int] = _id()
    report_id: Mapped[int | None] = _fk("drs_report.report_id", nullable=True)
    report_code: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="DRAFT", server_default="DRAFT")
    content_json: Mapped[Any] = mapped_column(JsonType, nullable=False)
    tested_hash: Mapped[str | None] = mapped_column(String(64))
    test_summary: Mapped[Any | None] = mapped_column(JsonType)
    author: Mapped[str] = mapped_column(String(100), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    reviewed_by: Mapped[str | None] = mapped_column(String(100))
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    review_note: Mapped[str | None] = mapped_column(String(2000))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now_updated()

    report: Mapped[Report | None] = relationship()


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


class ReportBiMember(Base):
    """One dataset of a report's embedded dashboard, and the report that feeds it (owner request
    2026-10-14: a dashboard may hold charts of several reports).

    Every DRS dataset is the result of one report (its ``bi_dataset_table``). A dashboard on the
    datasets of several reports is shown for one snapshot of each: the guest token carries one
    row-level rule per dataset. Written when the dashboard is attached (``selfservice.link_dashboard``);
    the report itself is one of its members when the dashboard reads its own dataset."""

    __tablename__ = "drs_report_bi_member"
    __table_args__ = (PrimaryKeyConstraint("report_id", "member_report_id"),)

    report_id: Mapped[int] = _fk("drs_report.report_id")
    member_report_id: Mapped[int] = _fk("drs_report.report_id")
    dataset_id: Mapped[int] = mapped_column(IdType, nullable=False)  # Superset's id of the dataset
    sort_order: Mapped[int] = _int(0)

    member: Mapped[Report] = relationship(foreign_keys=[member_report_id])


class ReportBiParam(Base):
    """A parameter of a member report put on the report's form for its dashboard. A parameter of
    a member that is not put there runs with its default value."""

    __tablename__ = "drs_report_bi_param"
    __table_args__ = (PrimaryKeyConstraint("report_id", "member_report_id", "param_name"),)

    report_id: Mapped[int] = _fk("drs_report.report_id")
    member_report_id: Mapped[int] = _fk("drs_report.report_id")
    param_name: Mapped[str] = mapped_column(String(50), nullable=False)
    sort_order: Mapped[int] = _int(0)


# --------------------------------------------------------------------------------------------
# Mirrors: copies of source tables in the DRS database (drscore.mirror)

MIRROR_MODES = ("full", "incremental")
MIRROR_STATUSES = ("ok", "error", "running")


class MirrorTable(Base):
    """One source table (or view) copied into the mirror schema on a schedule. Several mirrors may
    write one target table - the same table of several servers - each its own rows (``_source``)."""

    __tablename__ = "drs_mirror_table"
    __table_args__ = (
        CheckConstraint(_in("mode", MIRROR_MODES), name="mode"),
        CheckConstraint(f"last_status IS NULL OR {_in('last_status', MIRROR_STATUSES)}", name="last_status"),
        AUTOINC,
    )

    mirror_id: Mapped[int] = _id()
    mirror_code: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    datasource_id: Mapped[int] = _fk("drs_datasource.datasource_id", cascade=False)
    source_object: Mapped[str] = mapped_column(String(300), nullable=False)  # table or view, [schema.]name
    columns_json: Mapped[Any | None] = mapped_column(JsonType)  # source column names; empty: every column
    where_sql: Mapped[str | None] = mapped_column(Text)  # a filter in the source's own SQL
    target_table: Mapped[str] = mapped_column(String(63), nullable=False)
    key_columns_json: Mapped[Any | None] = mapped_column(JsonType)  # the row key; required by incremental
    mode: Mapped[str] = mapped_column(String(20), nullable=False, default="full", server_default="full")
    # incremental: only rows whose watermark column is above the last value read (minus lookback)
    watermark_column: Mapped[str | None] = mapped_column(String(128))
    watermark_value: Mapped[str | None] = mapped_column(String(100))
    lookback_seconds: Mapped[int] = _int(0)
    interval_minutes: Mapped[int] = _int(60)
    full_refresh_hours: Mapped[int | None] = mapped_column(Integer)  # incremental: a full copy this often
    run_hours: Mapped[str | None] = mapped_column(String(100))  # e.g. "0-6,22-23" (app timezone); empty: any
    full_hours: Mapped[str | None] = mapped_column(String(100))  # when the periodic full copy may start; empty: any
    read_uncommitted: Mapped[bool] = _bool(False)  # SQL Server: read without shared locks
    is_active: Mapped[bool] = _bool(True)
    last_started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_full_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_status: Mapped[str | None] = mapped_column(String(20))
    last_rows: Mapped[int | None] = mapped_column(Integer)
    last_duration_ms: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(String(2000))
    lock_owner: Mapped[str | None] = mapped_column(String(100))
    lock_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now_updated()

    datasource: Mapped[Datasource] = relationship()

    def __str__(self) -> str:
        return self.mirror_code


# --------------------------------------------------------------------------------------------
# Choice lists: choices ticked on a page and copied for the reports (drscore.choicelist)

class ChoiceList(Base):
    """A short list of choices - which kinds of item a report counts, say - ticked on a page by the
    person who decides, instead of being written into the report's SQL. DRS copies it into a table
    of the mirror schema (``target_table``) for the reports to join; a public link may be allowed to
    change the ticks."""

    __tablename__ = "drs_choice_list"
    __table_args__ = (AUTOINC,)

    list_id: Mapped[int] = _id()
    list_code: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    target_table: Mapped[str] = mapped_column(String(63), nullable=False, unique=True)
    is_active: Mapped[bool] = _bool(True)
    changed_by: Mapped[str | None] = mapped_column(String(100))  # who changed the ticks last
    changed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now_updated()

    items: Mapped[list["ChoiceItem"]] = relationship(back_populates="choice_list", cascade="all, delete-orphan",
                                                     order_by="(ChoiceItem.sort_order, ChoiceItem.item_key)")

    def __str__(self) -> str:
        return self.list_code


class ChoiceItem(Base):
    """One choice of a list: its key (what the reports join on), its label and whether it is ticked."""

    __tablename__ = "drs_choice_item"
    __table_args__ = (UniqueConstraint("list_id", "item_key"), AUTOINC)

    item_id: Mapped[int] = _id()
    list_id: Mapped[int] = _fk("drs_choice_list.list_id")
    item_key: Mapped[str] = mapped_column(String(100), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    group_name: Mapped[str | None] = mapped_column(String(100))
    detail: Mapped[str | None] = mapped_column(Text)  # what the choice covers, for the person who ticks
    sort_order: Mapped[int] = _int(0)
    is_checked: Mapped[bool] = _bool(False)
    changed_by: Mapped[str | None] = mapped_column(String(100))
    changed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    choice_list: Mapped[ChoiceList] = relationship(back_populates="items")


# --------------------------------------------------------------------------------------------
# Public links: a report opened without signing in (drscore.publiclink)

PUBLIC_VIEWS = ("bi", "grid", "html")


class PublicLink(Base):
    """A secret URL that shows one report, read-only, as one user sees it - for a screen on the wall,
    or a reader who has no DRS account. Only the hash of its token is kept."""

    __tablename__ = "drs_public_link"
    __table_args__ = (CheckConstraint(_in("view_key", PUBLIC_VIEWS), name="view_key"), AUTOINC)

    link_id: Mapped[int] = _id()
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)  # sha256 hex
    token_hint: Mapped[str] = mapped_column(String(8), nullable=False)  # its first characters, to tell links apart
    report_id: Mapped[int] = _fk("drs_report.report_id")
    run_as_user_id: Mapped[int] = _fk("drs_user.user_id")  # the report runs with this user's rights
    title: Mapped[str | None] = mapped_column(String(200))
    params_json: Mapped[Any | None] = mapped_column(JsonType)  # the parameter values it runs with
    editable_json: Mapped[Any | None] = mapped_column(JsonType)  # the parameters a reader may change
    view_key: Mapped[str] = mapped_column(String(10), nullable=False, default="bi", server_default="bi")
    refresh_seconds: Mapped[int | None] = mapped_column(Integer)  # the page reloads itself (a wall screen)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    is_active: Mapped[bool] = _bool(True)
    # True: anybody with the address sees the report, as run_as sees it. False (ORD 402): the address
    # opens the sign-in page, and the report runs as the signed-in reader, with their own rights.
    is_public: Mapped[bool] = _bool(True)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = _now()
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    use_count: Mapped[int] = _int(0)
    # the choice list a reader of this link may tick (its page reads and saves it): none = read-only
    choice_list_id: Mapped[int | None] = _fk("drs_choice_list.list_id", nullable=True, cascade=False)
    # the other sites whose pages may show this link in a frame (CSP frame-ancestors): "*" for any site,
    # or origins separated by spaces; none = this portal only (ORD 437, 1.8.0)
    frame_ancestors: Mapped[str | None] = mapped_column(String(1000))

    report: Mapped["Report"] = relationship()
    run_as: Mapped[User] = relationship()
    choice_list: Mapped[ChoiceList | None] = relationship()

    def __str__(self) -> str:
        return f"{self.token_hint}... ({self.report.report_code if self.report else self.report_id})"


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


ACTIVITY_OUTCOMES = ("OK", "DENIED", "ERROR")


class ActivityLog(Base):
    """Every request a user makes (drscore.activity): pages opened, every change, every refused or failed
    request - with what was sent, secrets left out. drs_access_log and drs_audit_log stay as they are."""

    __tablename__ = "drs_activity_log"
    __table_args__ = (
        CheckConstraint(_in("outcome", ACTIVITY_OUTCOMES), name="outcome"),
        Index(None, "logged_at"),
        Index(None, "username", "logged_at"),
        AUTOINC,
    )

    activity_id: Mapped[int] = _id()
    logged_at: Mapped[datetime] = _now()
    username: Mapped[str] = mapped_column(String(100), nullable=False)  # "-": not signed in
    action: Mapped[str] = mapped_column(String(60), nullable=False)  # e.g. design.save, admin.edit
    object_key: Mapped[str | None] = mapped_column(String(200))  # a report code, a draft id, ...
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    path: Mapped[str] = mapped_column(String(400), nullable=False)
    query_text: Mapped[str | None] = mapped_column(String(2000))
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome: Mapped[str] = mapped_column(String(10), nullable=False)
    detail_json: Mapped[Any | None] = mapped_column(JsonType)  # the fields sent, secrets masked
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    client_ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(400))
    request_id: Mapped[str | None] = mapped_column(String(32))


# --------------------------------------------------------------------------------------------
# Secrets kept encrypted in the DRS database (drscore.secretstore): the AI key, bot tokens

class Secret(Base):
    __tablename__ = "drs_secret"

    secret_name: Mapped[str] = mapped_column(String(60), primary_key=True)  # e.g. ai.api_key
    secret_ref: Mapped[str] = mapped_column(Text, nullable=False)  # enc:<Fernet token>, never the value
    updated_by: Mapped[str] = mapped_column(String(100), nullable=False)
    updated_at: Mapped[datetime] = _now_updated()


# --------------------------------------------------------------------------------------------
# Feedback (Góp ý)

FEEDBACK_KINDS = ("bug", "idea", "question")
FEEDBACK_URGENCIES = ("high", "normal", "low")
FEEDBACK_STATUSES = ("new", "seen", "doing", "done", "wontfix")
COMMENT_AUTHORS = ("user", "admin", "ai")


class Feedback(Base):
    __tablename__ = "drs_feedback"
    __table_args__ = (
        CheckConstraint(_in("kind", FEEDBACK_KINDS), name="kind"),
        CheckConstraint(_in("urgency", FEEDBACK_URGENCIES), name="urgency"),
        CheckConstraint(_in("status", FEEDBACK_STATUSES), name="status"),
        Index(None, "username"),
        Index(None, "status"),
        AUTOINC,
    )

    feedback_id: Mapped[int] = _id()
    username: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False, default="bug", server_default="bug")
    urgency: Mapped[str] = mapped_column(String(10), nullable=False, default="normal", server_default="normal")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    page_path: Mapped[str | None] = mapped_column(String(400))  # the page it is about
    app_version: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="new", server_default="new")
    done_version: Mapped[str | None] = mapped_column(String(20))
    ai_note: Mapped[str | None] = mapped_column(Text)  # Typhon's note for the administrators
    handled_by: Mapped[str | None] = mapped_column(String(100))
    handled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())  # the sender ticked the fix as checked
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now_updated()

    comments: Mapped[list["FeedbackComment"]] = relationship(
        back_populates="feedback", cascade="all, delete-orphan", order_by="FeedbackComment.comment_id")
    files: Mapped[list["FeedbackFile"]] = relationship(
        back_populates="feedback", cascade="all, delete-orphan", order_by="FeedbackFile.file_id")

    def __str__(self) -> str:
        return f"#{self.feedback_id}"


class FeedbackComment(Base):
    __tablename__ = "drs_feedback_comment"
    __table_args__ = (CheckConstraint(_in("author_kind", COMMENT_AUTHORS), name="author_kind"), AUTOINC)

    comment_id: Mapped[int] = _id()
    feedback_id: Mapped[int] = _fk("drs_feedback.feedback_id")
    username: Mapped[str] = mapped_column(String(100), nullable=False)
    author_kind: Mapped[str] = mapped_column(String(10), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _now()

    feedback: Mapped[Feedback] = relationship(back_populates="comments")


class FeedbackFile(Base):
    __tablename__ = "drs_feedback_file"
    __table_args__ = (AUTOINC,)

    file_id: Mapped[int] = _id()
    feedback_id: Mapped[int] = _fk("drs_feedback.feedback_id")
    file_name: Mapped[str] = mapped_column(String(200), nullable=False)  # as the sender named it
    media_type: Mapped[str] = mapped_column(String(100), nullable=False)  # from the file's first bytes
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    stored_name: Mapped[str] = mapped_column(String(100), nullable=False)  # under [feedback] files_dir
    created_at: Mapped[datetime] = _now()

    feedback: Mapped[Feedback] = relationship(back_populates="files")


# --------------------------------------------------------------------------------------------
# The AI agent (Typhon)

AI_STATUSES = ("ok", "refused", "error")


class AiConversation(Base):
    __tablename__ = "drs_ai_conversation"
    __table_args__ = (Index(None, "username"), AUTOINC)

    conversation_id: Mapped[int] = _id()
    username: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    messages_json: Mapped[Any] = mapped_column(JsonType, nullable=False)  # the turns sent to the model
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now_updated()


class AiUsage(Base):
    """One question to Typhon (or one feedback it read), answered or refused, with what it cost."""

    __tablename__ = "drs_ai_usage"
    __table_args__ = (
        CheckConstraint(_in("status", AI_STATUSES), name="status"),
        Index(None, "asked_at"),
        Index(None, "username", "asked_at"),
        AUTOINC,
    )

    usage_id: Mapped[int] = _id()
    asked_at: Mapped[datetime] = _now()
    username: Mapped[str] = mapped_column(String(100), nullable=False)
    purpose: Mapped[str] = mapped_column(String(20), nullable=False, default="ask", server_default="ask")
    conversation_id: Mapped[int | None] = mapped_column(IdType)  # no FK: the log outlives a conversation
    feedback_id: Mapped[int | None] = mapped_column(IdType)
    page_path: Mapped[str | None] = mapped_column(String(400))
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str | None] = mapped_column(Text)
    tools_json: Mapped[Any | None] = mapped_column(JsonType)  # every tool run: name, input, size of its result
    model: Mapped[str | None] = mapped_column(String(60))
    input_tokens: Mapped[int] = _int(0)
    output_tokens: Mapped[int] = _int(0)
    cache_read_tokens: Mapped[int] = _int(0)
    cache_write_tokens: Mapped[int] = _int(0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=Decimal(0),
                                              server_default=text("0"))
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    error_message: Mapped[str | None] = mapped_column(String(2000))
    duration_ms: Mapped[int | None] = mapped_column(Integer)


# --------------------------------------------------------------------------------------------
# Notices sent to Telegram / Zalo (drscore.notify)

class NoticeLog(Base):
    __tablename__ = "drs_notice_log"
    __table_args__ = (Index(None, "sent_at"), AUTOINC)

    notice_id: Mapped[int] = _id()
    sent_at: Mapped[datetime] = _now()
    channel: Mapped[str] = mapped_column(String(10), nullable=False)  # telegram / zalo
    chat_id: Mapped[str] = mapped_column(String(100), nullable=False)
    event: Mapped[str] = mapped_column(String(40), nullable=False)
    text_sent: Mapped[str] = mapped_column(Text, nullable=False)
    is_ok: Mapped[bool] = _bool(False)
    error_message: Mapped[str | None] = mapped_column(String(1000))  # the token is never written


ALL_TABLES = sorted(Base.metadata.tables)
