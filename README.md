# DRS - Dynamic Report System

A web portal for reports where a report is **data, not code**: rows in the DRS database say which
source to read, which SQL or procedure to run, which parameters to ask for, how to show the result
and who may see it. The full specification is `DRS_dynamic_report_system_spec.md`.

Status: **milestone 8 (admin pages, metadata export / import, audit log)** done, on top of
milestone 6 (BI with Superset, without row-level security in the guest token) and milestone 5 (design registry, HTML, report views as tabs) - the web portal on Tabler (sign-in, menu
by report group, report page with parameter form, Tabulator grid, XLSX / CSV / TXT export, account
page, Vietnamese / English), HTML reports rendered from design files, BI reports in link mode and
the "no design, no view" rule, on top of the report engine (SQL Server 2008 R2+, Oracle,
PostgreSQL, SQLite sources; JSON cache with history and lock), permissions and the dual-engine DRS
database.

## Quick look with the demo

```bash
python -m drs db upgrade
python -m drs seed-demo                       # prints the demo passwords once
python -m drs serve                           # http://127.0.0.1:8080, sign in as hr_a1a, hr_all, viewer or admin
```

Or from the command line:

```bash
python -m drs report run --code EMP_LIST --as-user hr_a1a
python -m drs grant show --user hr_all
```

## The portal

| Page | Address |
| --- | --- |
| Sign in | `/login` |
| Home: the user's report groups and reports, with search | `/` |
| A report: parameters, Run, grid, Refresh (with `can_refresh`), XLSX / CSV / TXT (with `can_export`) | `/reports/{code}` |
| Own account: change password, language | `/account` |
| Administration (administrators only): groups, reports, parameters, columns, row filters, datasources, users, attributes, roles, grants, logs - with Test run, Test connection, Unlock | `/admin/` |

Exports are made on the server from the snapshot on screen, with the same permission check and row
filter, and contain all rows the user may see (not only the filtered page).

JSON API: `GET /api/me`, `GET /api/menu`, `GET /api/reports/{code}`, `POST /api/reports/{code}/run`,
`GET /api/reports/{code}/export?format=xlsx|csv|txt&snapshot_id=`, `GET /healthz`. A state-changing
request carries the session's CSRF token in `X-CSRF-Token`.

## How the report cache works

- One table, `drs_report_snapshot`, holds the cached result (JSON) of every report.
- Each time someone opens a report, its retention (`drs_report.cache_ttl_seconds`) is checked.
  Still valid: the cached result is shown. Expired: the old snapshot is moved to
  `drs_report_snapshot_history` and the data is read again from the source.
- Reading again happens under a lock per (report, parameters): when several people open the same
  report at once the source runs once and everyone gets the same result.
- Nothing is deleted: `drs_report_snapshot_history` keeps every old snapshot, for every report.
  `cache purge` moves all expired snapshots there at once (for reports nobody opens); `cache clear
  --report CODE` moves all snapshots of one report.

## Report views: Grid, Layout (HTML), Dashboard (BI)

Every report opens with tabs:

| Tab | Shown when | What it shows |
| --- | --- | --- |
| Grid | the report has a query and `grid_enabled = true` (the default) | the rows, sort / filter / pages, XLSX / CSV / TXT export |
| Layout | `html_design_uri` is set (or `report_type = 'HTML'`) | the HTML design file, e.g. `file:html/att_summary.html` |
| Dashboard | `bi_design_uri` is set (or `report_type = 'BI'`) | a BI dashboard: `superset:<embedded uuid>` (embedded, guest token from DRS) or an `http(s)://` link |

`report_type` (GRID / HTML / BI, default GRID) is the tab the page opens on. All tabs show the same
data (one run, one snapshot); permissions and the row filter are the same on every tab.

A new report needs only its group, datasource and query: it appears in the menu at once and opens
on its grid. Designs come later, one `UPDATE` each, no restart:

```sql
UPDATE drs_report SET html_design_uri = 'file:html/att_summary.html' WHERE report_code = 'ATT_SUMMARY';
UPDATE drs_report SET bi_design_uri = 'https://bi.company.local/superset/dashboard/7/' WHERE report_code = 'HR_DASHBOARD';
```

A tab whose design is missing shows "This report has no design yet" (`DESIGN_NOT_SET`); the other
tabs work. `python -m drs report validate` lists the state of every design.

Print / PDF: the Layout tab and the Grid tab have a "Print / Save PDF" button that opens the
browser's print dialog (choose "Save as PDF"); the Grid prints every row the user may see. A
Superset dashboard is saved as PDF with Superset's own "Download" menu.

## BI with Apache Superset

A BI report with `bi_dataset_table` feeds a table of the DRS database (`drs_bi.<name>`) from its
result, following its retention; a Superset dashboard designed on that table is shown in the
Dashboard tab, embedded with a guest token issued by DRS (the user needs no Superset account).
Setup: `docker/compose.yml` and `docs/superset-setup.md`. Row-level security in the guest token is
not built yet: a report with row-filter rules cannot be embedded.

## Administration

Everything can be done without SQL on the admin pages (`/admin/`, administrators only, see
`docs/admin-guide.md`): every save is validated and written to the audit log. Reports designed on
SQLite move to PostgreSQL with `metadata export` / `metadata import`.

## Report sources

| `db_type` | Versions | Driver (`drs_datasource.driver`) | Notes |
| --- | --- | --- | --- |
| `mssql` | SQL Server 2008 R2 and later | empty = pyodbc + ODBC Driver 18; an ODBC driver name; `pymssql` | 2008 R2: use `pymssql`, or set `{"Encrypt": "no"}` in `options_json`. pyodbc needs the Microsoft ODBC driver (and unixODBC on Linux). |
| `oracle` | 12.1 and later (thin); 11.2 with `thick` | empty / `thin`; `thick` | `--database` is the service name; option `sid` to connect by SID; option `lib_dir` = Instant Client folder for thick mode. |
| `postgresql` | 10 and later (tested with 16) | - | Runs every report in a read-only transaction. |
| `sqlite` | - | - | `--database` is the file path; opened read-only. Used by the demo. |

Every connection setting is a column of `drs_datasource`, as a DBA tool such as Navicat or
DBeaver keeps it:

| Setting | Column | `datasource add` option |
| --- | --- | --- |
| Description | `description` | `--description` |
| Server, port | `host`, `port` | `--host`, `--port` |
| SQL Server named instance | `instance_name` | `--instance SQLEXPRESS` |
| Database (Oracle: service name, or SID) | `database_name`, `oracle_connect_by` | `--database`, `--sid` |
| Default schema (PostgreSQL, Oracle) | `default_schema` | `--schema` |
| Authentication | `auth_method`: password / windows / none | `--auth` |
| User, password | `username`, `secret_ref` (encrypted, or `env:VARIABLE`) | `--user`, hidden prompt or `--password-env` |
| SSL / encryption | `ssl_mode`: disable / prefer / require / verify; `trust_server_certificate` | `--ssl-mode`, `--trust-server-certificate` |
| Connect timeout | `connect_timeout_seconds` (default 15) | `--connect-timeout` |
| Driver | `driver` | `--driver` |
| Any other driver option | `options_json` | `--option KEY=VALUE` |

The password is asked at a hidden prompt and stored encrypted; `DRS_SECRET_KEY` must be set
(`python -m drs secret new-key` prints one).

```bash
python -m drs datasource add HRMS_PROD --type mssql --host 10.0.0.8 --database HRMS --user drs_ro \
    --description "HRMS production, read-only login"
python -m drs datasource add HRMS_OLD --type mssql --host 10.0.0.5 --instance SQL2008 --database HR \
    --user drs_ro --ssl-mode disable --driver "ODBC Driver 17 for SQL Server"
python -m drs datasource add ERP --type oracle --host 10.0.0.9 --database ORCLPDB1 --schema HR --user drs_ro
python -m drs datasource add DWH --type postgresql --host 10.0.0.7 --database dwh --schema report \
    --user drs_ro --ssl-mode require
python -m drs datasource show HRMS_PROD          # every setting, never the password
python -m drs datasource test HRMS_PROD          # connect and run a trivial query
python -m drs datasource set-password HRMS_PROD  # new password, encrypted
```

The login of a source must be read-only there (SELECT, and EXECUTE on the report procedures).

Every component is free (requirement R02); see `THIRD_PARTY_LICENSES.md`.

## Deploying on a server

`docs/deployment.md` is the full guide: PostgreSQL preparation, installation, configuration and
secrets, first start, Linux service (systemd) or Windows service (NSSM), HTTPS behind nginx / IIS,
source drivers, backup, upgrade, every configuration key, troubleshooting. Sample files are in
`deploy/`.

In short, with PostgreSQL:

```bash
psql -U postgres -f deploy/postgres/create_drs_database.sql   # login drs_app + database drs (edit the password)
python3 -m venv .venv && .venv/bin/pip install .
cp config/database.example.toml config/database.toml          # active = "postgresql", [postgresql] host / user
cp config/app.example.toml config/app.toml
.venv/bin/python -m drs secret new-key                        # once; keep the key safe
export DRS_DB_PASSWORD='...' DRS_SECRET_KEY='<the key>'      # a service reads them from /etc/drs/drs.env
.venv/bin/python -m drs serve          # creates schema "drs", every table and the administrator, then serves
```

`serve` connects, creates or migrates the DRS database (`[app] auto_migrate = true`), gives a
database without an administrator the default one (`admin` / `admin`, to be changed at the first
sign-in), checks the set-up (secret key, HTTPS cookie, administrator) and starts `[app] workers`
processes.

## Requirements

- Python 3.11 or newer
- For production: PostgreSQL 14 or newer. For design and test nothing else is needed (SQLite).

## First install

Windows (PowerShell):

```powershell
cd D:\Projects\DRS
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[test]"
Copy-Item config\database.example.toml config\database.toml
Copy-Item config\app.example.toml config\app.toml
python -m drs db upgrade
python -m drs db check
```

Linux:

```bash
cd /path/to/DRS
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[test]"
cp config/database.example.toml config/database.toml
cp config/app.example.toml config/app.toml
python -m drs db upgrade
python -m drs db check
```

The real config files are git-ignored; only the `*.example.toml` files are committed. The config
folder can be moved with the environment variable `DRS_CONFIG_DIR`.

## Choosing the DRS database engine

`config/database.toml` alone decides it:

```toml
active = "sqlite"        # or "postgresql"
```

To switch: change `active` (or set the environment variable `DRS_DB_ACTIVE`), then run
`python -m drs db upgrade`. No code changes.

For PostgreSQL, fill the `[postgresql]` section and put the password in the environment variable it
names (default `DRS_DB_PASSWORD`). The tables are created in the schema `schema` (default `drs`);
`db upgrade` creates the schema when it is missing.

## Secrets

`python -m drs secret new-key` prints a new encryption key. Put it in the environment variable named
by `app.secret_key_env` (default `DRS_SECRET_KEY`). It encrypts datasource passwords.

## Tests

```bash
python -m pytest                       # SQLite
DRS_TEST_PG_URL=postgresql://user:password@127.0.0.1:5432/dbname python -m pytest   # SQLite and PostgreSQL
```

With `DRS_TEST_PG_URL` set, every database test runs twice, once per engine. On PostgreSQL each
run uses a throw-away schema, dropped at the end. Use a test database, never a production one.

## Commands available so far

| Command | Purpose |
| --- | --- |
| `python -m drs db upgrade` | Create / migrate the DRS database on the active engine |
| `python -m drs db check` | Active engine, migration version, every table reachable |
| `python -m drs secret new-key` | Print a new encryption key |
| `python -m drs serve [--host H] [--port P] [--workers N]` | Create / migrate the DRS database if needed, check the set-up, start the web server |
| `python -m drs user add NAME [--display-name D] [--email E] [--admin] [--sso-subject S] [--no-password]` | Add a user; the password is asked at a hidden prompt |
| `python -m drs user list / disable / enable / set-password / set-admin [--off]` | Users |
| `python -m drs user attr set USER NAME VALUE... / remove USER NAME [VALUE] / list USER` | Row-filter attributes (`*` = every value) |
| `python -m drs role add CODE [--name N] / list / member add CODE USER / member remove CODE USER` | Roles |
| `python -m drs grant group CODE / grant report CODE --user U \| --role R [--export\|--no-export] [--refresh\|--no-refresh]` | Grants |
| `python -m drs revoke group CODE / revoke report CODE --user U \| --role R` | Remove a grant |
| `python -m drs grant show --user U` | What a user can see, and through which grant |

The first administrator is made by `serve`: a DRS database without an administrator gets user
`admin` with the password `admin`. That password opens only the account page, where it must be
replaced at the first sign-in. Another administrator:

```bash
python -m drs user add NAME --display-name "Administrator" --admin
```

| Command | Purpose |
| --- | --- |
| `python -m drs datasource add / list / test` | Report sources |
| `python -m drs report run --code C --as-user U [--param k=v ...] [--format grid\|json] [--refresh] [--out F]` | Run a report exactly as that user gets it |
| `python -m drs report validate [--code C]` | Check report definitions and design state |
| `python -m drs cache purge [--report C]` | Move every expired snapshot to the history |
| `python -m drs cache clear --report C` | Move every snapshot of one report to the history |
| `python -m drs cache warm [--report C]` | Run reports with their default parameters ahead of the users |
| `python -m drs metadata export --out F [--report C ...]` | Report definitions, datasources (no secrets), users (no passwords), roles and grants as one JSON file |
| `python -m drs metadata import --in F [--dry-run]` | Create / update them in this DRS database, matched by code (e.g. SQLite to PostgreSQL) |
| `python -m drs seed-demo [--force]` | Demo source, reports, role and users |
