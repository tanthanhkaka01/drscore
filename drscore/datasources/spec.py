"""Validation of a datasource definition before it is stored (CLI now, admin pages later)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DbType = Literal["mssql", "oracle", "postgresql", "sqlite"]
DEFAULT_PORTS = {"mssql": 1433, "oracle": 1521, "postgresql": 5432}


class DatasourceSpec(BaseModel):
    """Everything needed to connect to a source server, as a DBA tool (Navicat, DBeaver) asks for
    it. The password itself is not here: it is stored encrypted, or named as an environment
    variable."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{1,49}$")]
    name: Annotated[str, Field(max_length=200)] | None = None
    description: Annotated[str, Field(max_length=1000)] | None = None
    db_type: DbType
    host: Annotated[str, Field(max_length=200)] | None = None
    port: Annotated[int, Field(gt=0, lt=65536)] | None = None
    instance_name: Annotated[str, Field(max_length=100, pattern=r"^[A-Za-z0-9_$#-]+$")] | None = None
    database: Annotated[str, Field(min_length=1, max_length=500)]
    oracle_connect_by: Literal["service_name", "sid"] | None = None
    default_schema: Annotated[str, Field(max_length=128, pattern=r"^[A-Za-z_][A-Za-z0-9_$#]*$")] | None = None
    auth_method: Literal["password", "windows", "none"] = "password"
    username: Annotated[str, Field(max_length=200)] | None = None
    ssl_mode: Literal["disable", "prefer", "require", "verify"] | None = None
    trust_server_certificate: bool = False
    connect_timeout_seconds: Annotated[int, Field(gt=0, le=600)] | None = None
    driver: Annotated[str, Field(max_length=50)] | None = None
    options: dict[str, str] = {}

    @model_validator(mode="after")
    def _per_type(self) -> "DatasourceSpec":
        t = self.db_type
        if t == "sqlite":
            if self.host or self.port or self.username or self.driver or self.instance_name:
                raise ValueError("a sqlite datasource has only a file path (--database)")
            self.auth_method = "none"
            return self
        if not self.host:
            raise ValueError(f"a {t} datasource needs a host")
        if self.instance_name and t != "mssql":
            raise ValueError("instance_name is for SQL Server only")
        if self.oracle_connect_by and t != "oracle":
            raise ValueError("oracle_connect_by is for Oracle only")
        driver = (self.driver or "").lower()
        if t == "mssql" and driver and driver not in ("pyodbc", "pymssql") and not driver.startswith("odbc driver"):
            raise ValueError("mssql driver: pyodbc, pymssql or an ODBC driver name such as 'ODBC Driver 17 for SQL Server'")
        if t == "oracle" and driver not in ("", "thin", "thick"):
            raise ValueError("oracle driver: thin (default) or thick")
        if t == "postgresql" and driver:
            raise ValueError("a postgresql datasource has no driver choice")
        if self.auth_method == "windows" and (t != "mssql" or driver == "pymssql"):
            raise ValueError("Windows authentication is for SQL Server through ODBC")
        if self.auth_method == "password" and not self.username:
            raise ValueError("password authentication needs a user name")
        if self.default_schema and t == "mssql":
            raise ValueError("SQL Server has no per-connection default schema: it is the login's default schema")
        return self
