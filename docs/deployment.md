# Deploying DRS

How to install DRS on a server, run it as a service behind HTTPS, and keep it running. The DRS
database is SQLite by default - nothing to install; section 2 is for the installation that moves
to PostgreSQL. Every step below was run end to end on a clean copy (Linux, Python 3.13,
PostgreSQL 16): empty database -> `serve` -> schema and tables created -> sign in -> reports, admin
pages, datasource "Test connection".

Contents

1. [What you need](#1-what-you-need)
2. [Prepare PostgreSQL](#2-prepare-postgresql)
3. [Install DRS](#3-install-drs)
4. [Configure](#4-configure)
5. [First start](#5-first-start)
6. [Run as a service (Linux)](#6-run-as-a-service-linux)
7. [Run as a service (Windows)](#7-run-as-a-service-windows)
8. [HTTPS and the reverse proxy](#8-https-and-the-reverse-proxy)
9. [Report sources: drivers and logins](#9-report-sources-drivers-and-logins)
10. [Operations: logs, backup, upgrade](#10-operations-logs-backup-upgrade)
11. [Configuration reference](#11-configuration-reference)
12. [Troubleshooting](#12-troubleshooting)
13. [Security checklist](#13-security-checklist)

Sample files used below are in `deploy/`: `drs.env.example`, `systemd/drs.service`,
`nginx/drs.conf`, `postgres/create_drs_database.sql`, `windows/install-service.ps1`.

---

## 1. What you need

| | Minimum | Notes |
| --- | --- | --- |
| Server | 2 CPU, 4 GB RAM, 20 GB disk | More RAM for large reports: a snapshot is held in memory while it is built (`[limits] max_snapshot_mb`, default 200). |
| OS | Linux (Ubuntu 22.04+, RHEL 9+, Debian 12+) or Windows Server 2016+ | |
| Python | 3.11 or newer | `python3 --version` |
| PostgreSQL | optional: 14 or newer (tested with 16) | Instead of the default SQLite files, for the DRS database: report definitions, users, grants, cache, logs. Can be on another server. |
| Reverse proxy | nginx (Linux) or IIS + ARR (Windows) | For HTTPS. Optional on a trusted intranet. |
| Network | DRS -> PostgreSQL (5432), DRS -> each report source (1433 SQL Server, 1521 Oracle, 5432 PostgreSQL), users -> DRS (443) | |

Everything is free (see `THIRD_PARTY_LICENSES.md`). By default the DRS database is SQLite: two files
under `runtime/` - `drs.sqlite3` (DRS itself) and `bi/datasets.sqlite3` (the BI datasets, the only
file a BI tool is given) - with nothing to install, and a backup is a copy of them. Move to
PostgreSQL (section 2, then `active = "postgresql"` in `config/database.toml`) when several DRS
processes or servers must share one database, or when a DBA should own it.

## 2. Prepare PostgreSQL

Only for `active = "postgresql"`; with the default SQLite, go to section 3.

A PostgreSQL administrator runs this once (sample: `deploy/postgres/create_drs_database.sql`):

```sql
CREATE ROLE drs_app LOGIN PASSWORD 'a-long-random-password';
CREATE DATABASE drs OWNER drs_app ENCODING 'UTF8' TEMPLATE template0;
\connect drs
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON DATABASE drs FROM PUBLIC;
GRANT CONNECT ON DATABASE drs TO drs_app;
```

```bash
psql -U postgres -f deploy/postgres/create_drs_database.sql
```

That is all: **DRS creates its schemas and tables itself** on its first start (section 5). It
creates schema `drs` (all DRS tables) and, when the first BI dataset is loaded, schema `drs_bi`.

If the DBA prefers DRS not to own the database: create the database owned by someone else, grant
`CONNECT` to `drs_app`, and create the two schemas for it - DRS then creates the tables inside:

```sql
CREATE SCHEMA drs AUTHORIZATION drs_app;
CREATE SCHEMA drs_bi AUTHORIZATION drs_app;
```

DRS needs no superuser right. If `drs_app` may neither create a schema nor owns `drs`, the start
stops with "permission denied ... the DRS login must own the database or the schema".

Remote PostgreSQL: allow the DRS server in `pg_hba.conf`
(`hostssl drs drs_app <drs-server-ip>/32 scram-sha-256`) and set `sslmode = "require"` (or
`verify-full`) in `database.toml`.

## 3. Install DRS

### Linux

```bash
sudo useradd --system --home /opt/drs --shell /usr/sbin/nologin drs
sudo mkdir -p /opt/drs && sudo chown drs:drs /opt/drs
# copy the DRS folder (the release zip, or a git checkout) to /opt/drs, then:
cd /opt/drs
sudo -u drs python3 -m venv .venv
sudo -u drs .venv/bin/pip install .
sudo -u drs mkdir -p runtime
```

(Debian / Ubuntu: `sudo apt install python3-venv` first if `venv` is missing.)

### Windows (PowerShell)

```powershell
# copy the DRS folder to D:\DRS, then:
cd D:\DRS
py -3 -m venv .venv
.\.venv\Scripts\pip install .
```

`pip install .` installs DRS and its pinned dependencies into the virtual environment. The DRS folder
itself stays the home of `config/`, `runtime/` (logs) and `designs/` (HTML designs): the service
runs with that folder as working directory, or with the environment variable `DRS_HOME` naming it.

Without internet on the server: on a machine with internet and the same OS / Python version run
`pip download . -d wheels`, copy `wheels/` over, then `pip install --no-index --find-links wheels .`.

## 4. Configure

Two files in `config/`, copied from the examples (only the examples are in git; the real files are
ignored and hold no secret):

```bash
cp config/database.example.toml config/database.toml
cp config/app.example.toml config/app.toml
```

### config/database.toml - the PostgreSQL connection

```toml
active = "postgresql"

[postgresql]
host = "10.0.0.20"          # PostgreSQL server
port = 5432
database = "drs"
user = "drs_app"
password_env = "DRS_DB_PASSWORD"   # the NAME of the environment variable, never the password
schema = "drs"              # created by DRS when missing
bi_schema = "drs_bi"        # tables Superset reads (only with BI)
sslmode = "prefer"          # "require" / "verify-full" for a remote server
pool_size = 5               # connections per worker process
max_overflow = 5
```

Connections in use at most: `workers x (pool_size + max_overflow)`; keep it under PostgreSQL's
`max_connections`.

### config/app.toml - the important keys

```toml
[app]
name = "DRS"
base_url = "https://reports.company.local"  # the address users open
host = "127.0.0.1"          # 127.0.0.1 behind a reverse proxy; 0.0.0.0 to serve the network directly
port = 8080
default_locale = "vi"
timezone = "Asia/Ho_Chi_Minh"
workers = 2                 # web server processes (2-4)
forwarded_allow_ips = "127.0.0.1"   # the reverse proxy's address
auto_migrate = true         # create / migrate the DRS database at start

[session]
cookie_secure = true        # true with HTTPS
```

All keys are listed in [section 11](#11-configuration-reference).

### Secrets: environment variables

| Variable | What |
| --- | --- |
| `DRS_DB_PASSWORD` | password of `drs_app` (named by `password_env`) |
| `DRS_SECRET_KEY` | key that encrypts datasource passwords. Create it once: `.venv/bin/python -m drscore secret new-key`. **Keep a copy in a safe place** - without it the stored datasource passwords cannot be read and must be entered again. |
| `DRS_HOME` | the DRS folder, when the service does not start in it |
| `DRS_SUPERSET_PASSWORD` | only with Superset |
| any name given to `datasource add --password-env NAME` | that source's password |

Linux: put them in `/etc/drs/drs.env` (sample `deploy/drs.env.example`):

```bash
sudo mkdir -p /etc/drs
sudo cp deploy/drs.env.example /etc/drs/drs.env
sudo chmod 600 /etc/drs/drs.env && sudo chown root:root /etc/drs/drs.env
sudo nano /etc/drs/drs.env
```

Windows: the service script stores them in the service's own settings (section 7).

To run the CLI by hand on Linux with the same values:

```bash
cd /opt/drs
set -a; . /etc/drs/drs.env; set +a        # as root, or copy the values into your shell
.venv/bin/python -m drscore db check
```

## 5. First start

```bash
.venv/bin/python -m drscore serve
```

What happens:

```text
DRS database: postgresql (drs_app@10.0.0.20:5432/drs, schema drs)
DRS database created: revision (empty) -> 0008.
NOTE: the DRS database had no administrator: user "admin" was created with the password "admin". The first sign-in must change it.
DRS listening on http://127.0.0.1:8080 (2 workers); users open https://reports.company.local
```

1. DRS connects to PostgreSQL. A wrong host, password or right stops the start with a message that
   says which setting to check.
2. With `auto_migrate = true` it creates schema `drs` and every table, or applies the migrations of
   a newer DRS version. Two servers starting at once do not migrate together (PostgreSQL advisory
   lock). With `auto_migrate = false` it refuses to start until `python -m drscore db upgrade` is run -
   choose this if database changes must be done by a DBA at a planned time.
3. A database without an administrator is given the default one: user `admin`, password `admin`.
   That password opens only the account page: the first sign-in must replace it (at least
   `[auth.local] min_password_length` characters) before any other page, the admin pages or the
   API answer. Nothing is created when an administrator already exists (a disabled one counts),
   when another user is named `admin`, or with `[auth.local] enabled = false`; then add one with
   `python -m drscore user add NAME --display-name "..." --admin`.
4. It warns about a missing `DRS_SECRET_KEY`, a mismatch between `base_url` (https) and
   `cookie_secure`, a missing designs folder, and the lack of an active administrator.

Then, once:

```bash
.venv/bin/python -m drscore db check                                                 # every table "ok"
```

Open `base_url`, sign in as `admin` / `admin` and choose a new password; DRS then asks to sign in
again with it. **Do this right after the first start**: until then anyone who reaches the server
can do it instead. The menu entry "Administration" (`/admin/`) manages
everything else without SQL: datasources (with "Test connection"), report groups, reports
("Test run"), parameters, columns, users, roles, grants, logs (see `docs/admin-guide.md`).

To look around first: `python -m drscore seed-demo` adds a demo source, six reports and demo users (it
prints their passwords; an existing `admin` is kept). Remove them later in the admin pages, or do
not run it on production.

Reports designed on a test DRS (SQLite) are carried over with
`python -m drscore metadata export --out reports.json` there and
`python -m drscore metadata import --in reports.json` here (then `datasource set-password` for each
source).

`GET /healthz` answers `{"status": "ok", "database": "postgresql"}` - use it for monitoring.

## 6. Run as a service (Linux)

```bash
sudo cp deploy/systemd/drs.service /etc/systemd/system/drs.service
# edit User / WorkingDirectory / paths if DRS is not in /opt/drs
sudo systemctl daemon-reload
sudo systemctl enable --now drs
sudo systemctl status drs
journalctl -u drs -f          # start-up messages; the application log is runtime/logs/drs.log
```

The unit runs `python -m drscore serve` as user `drs`, with `/etc/drs/drs.env`, restarts on failure,
and may write only to `/opt/drs/runtime`.

### Run in containers (Docker)

Instead of a Python environment and a systemd unit: `deploy/docker/docker-compose.yml`. The image
(`deploy/docker/Dockerfile`) carries Python and drscore, nothing else; everything of the
installation stays in one folder on the host:

```
/opt/drs/
  docker-compose.yml     deploy/docker/docker-compose.yml
  .env                   DRS_SECRET_KEY (chmod 600); DRS_PORT / DRS_UID / DRS_GID when not 8080 / 1000 / 1000
  config/                app.toml, database.toml - copies of config/*.example.toml
  designs/               HTML designs
  runtime/               logs, and with the default SQLite the two database files
  src/                   the drscore source the image is built from
```

In `config/app.toml`: `host = "0.0.0.0"`, and `base_url` = the address users open. `DRS_PORT` in
`.env` is `[app] port`.

```bash
cd /opt/drs
mkdir -p config designs runtime/bi
docker compose up -d --build            # first start, and after src/ was replaced (an upgrade)
docker compose restart drscore          # after config/ changed; designs/ needs no restart
docker compose logs -f drscore          # start-up messages; the application log is runtime/logs/drs.log
docker compose exec drscore python -m drscore db check
```

The container restarts with the server (`restart: unless-stopped`) and runs as the owner of the
folder (`DRS_UID` / `DRS_GID`). With PostgreSQL instead of SQLite: `active = "postgresql"` in
`config/database.toml` and `DRS_DB_PASSWORD` in `.env`.

**SQL Server sources.** The image has no Microsoft ODBC driver: it is Microsoft's software under
Microsoft's licence, and drscore does not distribute it. Either give the datasource
`driver = "pymssql"` (open source, in the image), or build your own image with the driver -
`deploy/docker/Dockerfile.msodbc` downloads it from Microsoft, and building it is your acceptance
of Microsoft's terms - and name that image in `docker-compose.yml`.

**Apache Superset** is optional, as profile `bi` of the same file (`docs/superset-setup.md`):

```bash
# superset.env (chmod 600): SUPERSET_SECRET_KEY, SUPERSET_GUEST_TOKEN_SECRET, DRS_PORTAL_ORIGIN
docker compose --profile bi run --rm superset-init     # once: its tables, and admin / admin
docker compose --profile bi up -d --build
```

Superset keeps its own data in a SQLite file on the volume `superset-home`, or in PostgreSQL when
`superset.env` sets `SUPERSET_DB_PASSWORD`. `[bi.superset] base_url` is the address a browser
reaches Superset at, `api_url = "http://superset:8088"`, and `DRS_PORTAL_ORIGIN` is DRS's
`base_url` - also the "allowed domain" of every embedded dashboard. `DRS_SECRET_KEY` and
`SUPERSET_SECRET_KEY` must never change: passwords stored in the databases are encrypted with them.

## 7. Run as a service (Windows)

With NSSM (free, public domain, <https://nssm.cc>), from an elevated PowerShell in the DRS folder:

```powershell
.\deploy\windows\install-service.ps1 -DbPassword '...' -SecretKey '...'
Get-Service DRS
```

The script registers `python -m drscore serve` as service "DRS" (automatic start), stores the
environment variables in the service's registry key (administrators only), and writes console
output to `runtime\logs\service.*.log`. Stop / start: `Stop-Service DRS`, `Start-Service DRS`.
Remove: `nssm remove DRS confirm`.

For the CLI in a PowerShell window, set the variables first:
`$env:DRS_DB_PASSWORD='...'; $env:DRS_SECRET_KEY='...'; .\.venv\Scripts\python -m drscore db check`.

## 8. HTTPS and the reverse proxy

DRS listens on `127.0.0.1:8080`; the proxy terminates HTTPS. nginx sample: `deploy/nginx/drs.conf`.

```nginx
location / {
    proxy_pass http://127.0.0.1:8080;
    proxy_set_header Host $host;                 # required
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 900s;
    proxy_buffering off;
}
```

- `Host` must be passed unchanged: the admin pages refuse a form whose Origin is not the Host
  (protection against cross-site requests). Symptom when it is missing: every save in `/admin/`
  answers "403 - cross-site request refused".
- In `app.toml`: `base_url = "https://..."`, `[session] cookie_secure = true`,
  `forwarded_allow_ips` = the proxy's address (`127.0.0.1` when on the same server). Then the access
  log records the users' real IP addresses.
- IIS: install URL Rewrite and Application Request Routing (both free), enable the proxy in ARR,
  add a reverse-proxy rule to `http://127.0.0.1:8080`, and set ARR "Preserve host header"
  (`appcmd set config -section:system.webServer/proxy -preserveHostHeader:true /commit:apphost`).
- Without a proxy on a trusted intranet: `host = "0.0.0.0"`, keep `cookie_secure = false`, and open
  `http://server:8080`.

## 9. Report sources: drivers and logins

| Source | What the DRS server needs |
| --- | --- |
| PostgreSQL | nothing (psycopg is installed with DRS) |
| Oracle 12.1+ | nothing (python-oracledb thin mode). Oracle 11.2: Oracle Instant Client + `driver = "thick"`, option `lib_dir` |
| SQL Server 2012+ | Microsoft ODBC Driver 18 for SQL Server (free). Linux: Microsoft's package repository, `msodbcsql18` + `unixODBC`; Windows: the installer from Microsoft. Or `driver = "pymssql"`, which needs nothing. The ODBC driver is Microsoft's software under Microsoft's licence: DRS does not contain it, and neither does the container image (`deploy/docker/Dockerfile.msodbc` adds it to an image you build yourself) |
| SQL Server 2008 R2 | `driver = "pymssql"` (installed with DRS), or ODBC Driver 17/18 with `Encrypt=no` |

Add a source in the admin pages (Datasources -> Create, "New password", then "Test connection") or
with `python -m drscore datasource add` (see `README.md`). Give DRS a **read-only** login on each
source (SELECT, and EXECUTE on report procedures).

## 10. Operations: logs, backup, upgrade

**Logs**

- `runtime/logs/drs.log` - application log, one line per request (time, request id, user, route,
  duration), rotated daily, 30 days kept.
- Table `drs_access_log` - who opened / ran / exported which report (admin pages: Logs -> Access
  log). Table `drs_audit_log` - every change of definitions, users and grants.
- The cache needs no job: expired snapshots move to `drs_report_snapshot_history` when a report is
  opened; `python -m drscore cache purge` moves the rest (schedule it nightly if you like).

**Backup**

```bash
pg_dump -U drs_app -h 10.0.0.20 -Fc drs > drs_$(date +%F).dump     # nightly
pg_restore -U drs_app -h 10.0.0.20 -d drs --clean drs_2026-10-07.dump
```

Back up also: `config/*.toml`, `designs/`, and the value of `DRS_SECRET_KEY` (kept apart from the
dump). `drs_report_snapshot_history` grows over time; it can be excluded with
`--exclude-table-data=drs.drs_report_snapshot_history` if old snapshots need not be restored.

**Upgrade to a new DRS version**

```bash
sudo systemctl stop drs
pg_dump ... > before_upgrade.dump
# replace the DRS folder's code with the new version (keep config/, runtime/, designs/)
sudo -u drs .venv/bin/pip install .
sudo systemctl start drs          # auto_migrate applies the new migrations; journalctl shows them
```

With `auto_migrate = false`: run `python -m drscore db upgrade` before starting.

**Move from SQLite to PostgreSQL**: `metadata export` on the SQLite DRS, prepare PostgreSQL
(section 2), set `active = "postgresql"`, start (tables are created), `metadata import`,
`datasource set-password` for each source. Users are imported without passwords
(`user set-password`).

## 11. Configuration reference

### database.toml

| Key | Default | Meaning |
| --- | --- | --- |
| `active` | - | `"sqlite"` or `"postgresql"`; the environment variable `DRS_DB_ACTIVE` overrides it |
| `[sqlite] path` | `runtime/drs.sqlite3` | file of the SQLite DRS database (relative to the DRS folder) |
| `[sqlite] bi_path` | `runtime/bi/datasets.sqlite3` | file of the BI datasets on SQLite, in a folder of its own: the only thing a BI tool is given to read |
| `[postgresql] host`, `port` | `127.0.0.1`, `5432` | server |
| `database` | `drs` | database name |
| `user` | `drs_app` | login |
| `password_env` | `DRS_DB_PASSWORD` | name of the environment variable with the password |
| `schema` | `drs` | schema of the DRS tables (created when missing) |
| `bi_schema` | `drs_bi` | schema of the BI dataset tables |
| `sslmode` | `prefer` | `disable` / `allow` / `prefer` / `require` / `verify-ca` / `verify-full` |
| `pool_size`, `max_overflow` | `5`, `5` | connection pool per worker |

### app.toml

| Section / key | Default | Meaning |
| --- | --- | --- |
| `[app] name` | `DRS` | title shown in the portal |
| `base_url` | `http://localhost:8080` | address users open (checked against `cookie_secure`) |
| `host`, `port` | `127.0.0.1`, `8080` | where the server listens (`serve --host / --port` override) |
| `default_locale` | `vi` | `vi` / `en` until the user chooses |
| `timezone` | `Asia/Ho_Chi_Minh` | dates of tokens such as `@today`, display of times |
| `secret_key_env` | `DRS_SECRET_KEY` | name of the variable with the encryption key |
| `workers` | `1` | server processes (`serve --workers` overrides) |
| `forwarded_allow_ips` | `127.0.0.1` | proxies trusted for `X-Forwarded-For` / `-Proto` (comma list, `*` = any) |
| `auto_migrate` | `true` | `serve` creates / migrates the DRS database before starting |
| `[session] idle_minutes` | `60` | sign-out after this long without a request |
| `absolute_hours` | `12` | sign-out this long after sign-in |
| `cookie_secure` | `false` | `true` with HTTPS |
| `[auth.local] enabled` | `true` | sign-in with DRS passwords |
| `min_password_length` | `10` | password policy |
| `max_failed_logins`, `lockout_minutes` | `5`, `15` | lock after wrong passwords ("Unlock" in the admin pages) |
| `[auth.sso] ...` | `enabled = false` | single sign-on: not built yet (milestone 7); leave disabled |
| `[designs] root` | `designs` | folder of the HTML design files |
| `[cache] lock_wait_seconds` | `180` | how long a second viewer waits while the first one's query runs |
| `lock_stale_seconds` | `900` | a lock older than this is taken over |
| `[limits] default_max_rows`, `hard_max_rows` | `50000`, `500000` | rows per report (a report's `max_rows` cannot exceed the hard limit) |
| `default_timeout_seconds`, `hard_timeout_seconds` | `120`, `900` | query time per report |
| `max_snapshot_mb` | `200` | largest result kept |
| `[export] header_source` | `label` | export headers: column label or field name |
| `csv_delimiter`, `csv_bom` | `,`, `true` | CSV; the BOM lets Excel read Vietnamese |
| `txt_delimiter`, `txt_delimiter_replacement`, `txt_line_ending` | `\|`, `¦`, `crlf` | TXT export |
| `formula_guard` | `true` | CSV text cells starting with `= + - @` (or tab / CR) get a `'` prefix so Excel does not run them as formulas |
| `[bi.superset] ...` | `enabled = false` | see `docs/superset-setup.md` |

### Environment variables

`DRS_HOME` (DRS folder), `DRS_CONFIG_DIR` (config folder, default `<DRS_HOME>/config`),
`DRS_DB_ACTIVE` (overrides `active`), `DRS_DB_PASSWORD`, `DRS_SECRET_KEY`, `DRS_SUPERSET_PASSWORD`,
and the names used by `password_env` / `--password-env`.

## 12. Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `Configuration file not found: .../config/database.toml` | copy the `*.example.toml` files (section 4); or the service does not run in the DRS folder - set `DRS_HOME` |
| `The DRS database password is not set: define the environment variable DRS_DB_PASSWORD` | the variable is missing in the service's environment |
| `Cannot connect to the DRS database ... password authentication failed` | wrong password, or `pg_hba.conf` does not allow the DRS server |
| `... permission denied for database drs` | `drs_app` may not create schema `drs`: make it the database owner, or create the schema for it (section 2) |
| `The DRS database is at revision ..., this version needs ...` | `auto_migrate = false`: run `python -m drscore db upgrade` |
| Sign-in "works" but you land on the sign-in page again | `cookie_secure = true` while the site is opened over http |
| Every save in `/admin/` gives "403 - cross-site request refused" | the proxy does not pass the `Host` header (section 8) |
| Access log shows the proxy's IP for everyone | `forwarded_allow_ips` does not list the proxy |
| "Test connection" of a SQL Server source: `Can't open lib 'ODBC Driver 18 for SQL Server'` | install the Microsoft ODBC driver (section 9), or use `driver = "pymssql"` |
| `The encryption key is not set` when saving a datasource password | `DRS_SECRET_KEY` missing in the service environment |
| `DATASOURCE_UNAVAILABLE ... the password cannot be decrypted with the current key` | `DRS_SECRET_KEY` changed: restore the old key, or set the passwords again |

`python -m drscore db check` and `python -m drscore report validate` are the first two commands to run.

## 13. Security checklist

- [ ] The default password of `admin` is replaced (sign in once, right after the first start)
- [ ] HTTPS in front, `cookie_secure = true`, `base_url` with `https://`
- [ ] DRS listens on `127.0.0.1` behind the proxy (or the port is firewalled)
- [ ] `/etc/drs/drs.env` is `chmod 600`; no password in any `.toml` file
- [ ] `DRS_SECRET_KEY` backed up apart from the database dumps
- [ ] `drs_app` is not a superuser; PostgreSQL reachable only from the DRS (and Superset) server
- [ ] Each report source uses a read-only login
- [ ] The demo (`seed-demo`) is not loaded on production, or its users are removed
- [ ] Nightly `pg_dump`, tested restore
- [ ] Administrators are few; their changes are in Logs -> Audit log
