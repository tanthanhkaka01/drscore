# Apache Superset with DRS

Superset (Apache-2.0, open source) is the BI layer: dashboards are designed there by drag and
drop; DRS decides who may open them, keeps their data fresh and shows them inside the portal.

Status: dataset feed, link mode and embedded mode with a DRS-issued guest token are built.
**Row-level security in the guest token is not built yet** (owner decision, 2026-10-06): until it
is, a report that has row-filter rules cannot be embedded (DRS answers `REPORT_MISCONFIGURED`
instead of showing rows the viewer may not see). Everything below was written for Superset 6.1.0;
check setting names against the documentation of the version you install. It was first run end to
end on 2026-10-07 (Docker Desktop on Windows, the DRS database on another server): what that run
found is in "Troubleshooting" at the end.

## 1. Start PostgreSQL and Superset (Docker Engine on Linux)

```bash
cp docker/.env.example docker/.env          # change every password and key
docker compose -f docker/compose.yml --env-file docker/.env up -d postgres
docker compose -f docker/compose.yml --env-file docker/.env run --rm superset-init
docker compose -f docker/compose.yml --env-file docker/.env up -d superset
```

`docker/postgres/init.sh` runs when the PostgreSQL volume is created:

| Login | Can do |
| --- | --- |
| `drs_app` | owns database `drs`: schema `drs` (DRS tables) and schema `drs_bi` (dataset tables) |
| `superset` | owns database `superset` (Superset's own metadata) |
| `superset_reader` | reads `drs_bi` only. It cannot read `drs` (password hashes, snapshots, grants) and cannot create tables. New dataset tables are readable automatically (default privileges). |

These rights were checked on PostgreSQL 16: `superset_reader` reads `drs_bi.<table>` before and
after DRS replaces it, and gets `permission denied` on schema `drs`.

`POSTGRES_BIND` / `SUPERSET_BIND` in `docker/.env` choose the address of the host the two ports are
published on: `127.0.0.1` for a trial on one PC, `0.0.0.0` when other people's browsers must reach
Superset. `SUPERSET_PORT` must stay a plain number: the Superset image reads it too.

### The DRS database on another server

Then the PostgreSQL of this compose file only holds Superset's own metadata, and Superset reads
`drs_bi` on the DRS server. A DBA creates a login there (any name; it needs no right of its own):

```sql
CREATE ROLE superset_reader LOGIN PASSWORD '...';
GRANT CONNECT ON DATABASE drs TO superset_reader;
```

and the owner of the DRS database (the DRS login) lets it read the dataset schema, tables created
later included - DRS replaces a dataset table on every refresh:

```sql
GRANT USAGE ON SCHEMA drs_bi TO superset_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA drs_bi TO superset_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE <drs login> IN SCHEMA drs_bi GRANT SELECT ON TABLES TO superset_reader;
```

Schema `drs_bi` exists after the first dataset was loaded (`python -m drscore cache warm --report CODE`
for a report with `bi_dataset_table`). The connection of step 3.1 then names that server; a
password with special characters is URL-encoded in the address (`#` is `%23`).

### Superset's own data in a schema of the DRS database

Superset's users, dashboards and charts can live on that server too, in one schema of the DRS
database, so nothing is kept in the PostgreSQL of this compose file. The owner of the DRS database
creates the schema and lets the Superset login create its tables there:

```sql
CREATE SCHEMA superset;
GRANT USAGE, CREATE ON SCHEMA superset TO <superset login>;
```

and `docker/.env` names the place (the password is written as it is, special characters included):

```
SUPERSET_DB_HOST=<server>      SUPERSET_DB_PORT=5432     SUPERSET_DB_NAME=drs
SUPERSET_DB_USER=<superset login>   SUPERSET_DB_SCHEMA=superset   SUPERSET_DB_PASSWORD=...
```

Each on its own line. `superset-init` then creates the tables in that schema; start `superset` with
`--no-deps` when the `postgres` service is not used. To move an installation that already has
dashboards: stop Superset, copy its database (`CREATE DATABASE superset_move TEMPLATE superset`),
`ALTER SCHEMA public RENAME TO superset` in the copy, `pg_dump -n superset --no-owner
--no-privileges --no-comments` it, and run the dump (without its `CREATE SCHEMA` line) on the
server as the Superset login. `SUPERSET_SECRET_KEY` must stay the same: the stored connection
passwords are encrypted with it. With the same login as the connection of step 3.1, whoever may
write SQL on that connection can read Superset's own tables: give SQL Lab to administrators only,
or use two logins.

### DRS on SQLite (the default): the datasets file

With `active = "sqlite"` no PostgreSQL is needed at all. DRS writes the datasets into a file of
their own, `runtime/bi/datasets.sqlite3` (`[sqlite] bi_path`), one table per report, named as the
report's "BI dataset table". Superset is given that folder read-only and nothing else of DRS: the
DRS database, with its password hashes and stored secrets, is another file it never sees.
`deploy/docker/docker-compose.yml` does it (profile `bi`): the folder is mounted at `/drs_bi`,
Superset keeps its own data in a SQLite file on a volume, and the connection of step 3.1 is

```
sqlite:///file:/drs_bi/datasets.sqlite3?mode=ro&uri=true
```

(written with `file:` - the plain `sqlite:////drs_bi/...` form does not open a read-only folder).
Superset refuses SQLite connections unless `PREVENT_UNSAFE_DB_CONNECTIONS = False`;
`superset_config.py` sets it when `DRS_BI_SQLITE` is defined, as that compose file does. Checked on
Superset 6.1.0: the connection lists the dataset tables, a query returns their rows, and a write
through it is refused. Steps 3.2 to 3.6 and 4 are the same; in step 3.2 the schema is `main`.

## 2. Point DRS at that PostgreSQL

Only for `active = "postgresql"`. `config/database.toml`: `database = "drs"`, `user = "drs_app"`,
`schema = "drs"`, `bi_schema = "drs_bi"`, password in `DRS_DB_PASSWORD`. Then
`python -m drscore db upgrade`.

## 3. In Superset (as admin, http://localhost:8088)

First sign-in: user **admin**, password **admin** (`SUPERSET_ADMIN_USER` / `SUPERSET_ADMIN_PASSWORD`
in `docker/.env`). Superset then shows only its "Reset Password Form" - every other page leads back
to it, and an API call made with that browser session answers 403 - until another password is
saved; after that it works normally and `admin` no longer signs in. The check is in
`docker/superset/superset_config.py` (`FLASK_APP_MUTATOR`): a user whose password is still the
initial one must change it. **It guides the person who signs in; it is not a lock**: a program that
asks Superset's API for a token with admin / admin (`/api/v1/security/login`) is not stopped
(measured on 6.1.0), so change the password right after the first start. It does not
concern the viewers of embedded dashboards (guest tokens issued by DRS) nor users without a local
password. Superset accounts are separate from DRS accounts: only the people who design dashboards
need one (Settings > List Users); DRS viewers never do.

1. **Database connection** (Settings > Database Connections > + Database > PostgreSQL):
   `postgresql+psycopg2://superset_reader:<SUPERSET_READER_PASSWORD>@postgres:5432/drs`.
   Use this login only - never `drs_app`.
2. **Datasets**: Datasets > + Dataset > schema `drs_bi` > the name in "BI dataset table" of the
   report. It exists from the first run of the report on: open the report, choose its parameters,
   run it - or **Create BI table** on its admin page, or `python -m drscore cache warm --report
   CODE`, which run it with the default parameters. See "What a dataset is" below: it is the
   report's result, not a copy of one run, and it has one column of its own, `drs_snapshot_id`.
3. **Design the dashboard** with charts on that dataset (drag and drop).
4. **Guest role**: Settings > List Roles > + `DRS_Embedded` with read access to dashboards and
   charts and `datasource access` on the `drs_bi` datasets used by embedded dashboards. Its name
   is `GUEST_ROLE_NAME` in `docker/superset/superset_config.py`. A copy of Superset's own `Gamma`
   role ("Copy role") works as it is.
5. **Service account for DRS**: Settings > List Users > + `drs_service` with a role that has
   `can grant guest token on SecurityRestApi`, `can read on SecurityRestApi` (the CSRF token),
   `can read on EmbeddedDashboard` and `can read on Dashboard` - nothing more. Its password
   goes into the environment variable named by `[bi.superset] password_env` (default
   `DRS_SUPERSET_PASSWORD`) on the DRS server.
6. **Embed the dashboard**: open it > ... > Embed dashboard > allowed domains = the address of the
   DRS portal (`DRS_PORTAL_ORIGIN`) > Enable embedding. Copy the UUID it shows.

## 4. Register the dashboard in DRS

```sql
UPDATE drs_report
SET bi_design_uri = 'superset:<the uuid from step 3.6>', bi_dataset_table = 'hr_headcount'
WHERE report_code = 'HR_DASHBOARD';
```

`config/app.toml`:

```toml
[bi.superset]
enabled = true
base_url = "https://bi.company.local"     # as the browser reaches Superset
api_url = "http://superset:8088"          # as the DRS server reaches Superset
username = "drs_service"
password_env = "DRS_SUPERSET_PASSWORD"
guest_token_ttl_seconds = 300             # = GUEST_TOKEN_JWT_EXP_SECONDS in superset_config.py
check_design = true                       # DRS asks Superset that the dashboard exists (cached 60 s)
```

The report's Dashboard tab now shows the dashboard. The user needs no Superset account: the DRS
server checks the user's permission on the report and asks Superset for a guest token limited to
that one dashboard (`POST /api/reports/{code}/bi-token`, logged as `BI_TOKEN`).

## Self-service (1.3.0)

In DRS 1.3.0, after an administrator completes a one-time configuration, **the administrator does
nothing per report**. A designer publishes a report, DRS drives Superset automatically via its
REST API, the team's dashboard author designs the dashboard in Superset, and the designer attaches
it to the report on the BI page (`/design/reports/{code}/bi`).

### The five `[bi.superset]` keys
Configure the self-service keys in `config/app.toml`:

```toml
[bi.superset]
enabled = true
base_url = "http://bi.example.local:8088"    # as the browser reaches Superset
api_url = "http://bi.example.local:8088"     # as the DRS server reaches Superset
username = "drs_service"                     # guest token service account
password_env = "DRS_SUPERSET_PASSWORD"
guest_token_ttl_seconds = 300
check_design = true                          # verify the dashboard exists before showing it

# Self-service keys (1.3.0):
self_service = true                          # DRS makes datasets, roles and embedding in Superset
admin_username = "admin"                     # a Superset account with the Admin role, for that
admin_password_env = "DRS_SUPERSET_ADMIN_PASSWORD" # environment variable holding the admin password
database_name = "DRS"                        # the Superset database connection over the bi schema
design_role_prefix = "DRS_DESIGN_"           # one Superset role per report group: DRS_DESIGN_<GROUP_CODE>
```

Self-service is active when `enabled = true`, `self_service = true`, and `admin_username` is set,
with the environment variable named by `admin_password_env` (`DRS_SUPERSET_ADMIN_PASSWORD`) defined.

### Why DRS drives Superset with an admin account
DRS signs in to Superset with an account having the **Admin** role (`admin_username` and
`DRS_SUPERSET_ADMIN_PASSWORD`). Superset's REST API requires administrative privileges to:
- Find or create the dataset `drs_bi.<table>` on the database connection named by `database_name`.
- Refresh dataset columns (`PUT /api/v1/dataset/<id>/refresh`) after report query or column changes.
- Create the group role `<design_role_prefix><GROUP_CODE>` (e.g. `DRS_DESIGN_SALES`) in Superset.
- Grant `"datasource access on [<db>].[<table>](id:<n>)"` to `<design_role_prefix><GROUP_CODE>`
  (and ensure it is in no other `DRS_DESIGN_` role; if a report moves groups, the permission moves too).
- Enable embedding of a dashboard (`POST /api/v1/dashboard/<id>/embedded`) with allowed domains
  set to the origin of `[app] base_url`.
- Discover which dashboards use a dataset (`GET /api/v1/dataset/<id>/related_objects`).

By contrast, the guest-token service account (`drs_service`) remains minimally privileged with only
token issuance rights.

### What DRS does automatically
- Automatically names the BI dataset table `report_code.lower()` when a report is published or
  approved (appending `_2`, `_3` if a name conflict exists).
- Runs `prepare_dataset` in a background thread to execute the report with default parameters and
  refresh the PostgreSQL view `drs_bi.<table>`.
- Syncs with Superset: creates or finds the dataset, refreshes columns, and updates permissions in
  the group's `DRS_DESIGN_<GROUP_CODE>` role.
- Discovers dashboards built on the dataset and displays usability status on the BI page.
- Enables dashboard embedding and links `bi_design_uri = superset:<uuid>` when the designer clicks
  **Use for this report**.

### One-time administrator setup
1. **Database migration**: Run `python -m drscore db upgrade` (migration `0010_can_publish`).
2. **Superset configuration**: Set the five `[bi.superset]` keys in `config/app.toml` and export
   `DRS_SUPERSET_ADMIN_PASSWORD`.
3. **Database connection in Superset**: Ensure a database connection named `database_name` (e.g. `DRS`)
   exists in Superset connecting to PostgreSQL with read access to schema `drs_bi`.
4. **Pre-create group design role**: Run:
   ```bash
   python -m drscore bi sync --group <GROUP_CODE>
   ```
   (e.g. `python -m drscore bi sync --group SALES`). This creates the role `DRS_DESIGN_<GROUP_CODE>`
   in Superset before assigning it.
5. **Create Superset dashboard designers**: In Superset (**Settings > List Users**), create users
   with roles `Gamma` + `DRS_DESIGN_<GROUP_CODE>` (and Active).
   > [!IMPORTANT]
   > Never assign `Alpha` or `sql_lab` to dashboard designers. `DRS_Embedded` is the guest role,
   > never for a person.
6. **Configure DRS designer**: In DRS, grant the designer `--design` and `--publish` on the group,
   plus the datasource grant:
   ```bash
   python -m drscore grant group SALES --user designer --design --publish
   python -m drscore grant datasource SALES_DB --user designer
   ```

### Restricted reports and row filters
- **Restricted reports** (`is_restricted = true`): Never placed into any `DRS_DESIGN_` role.
  Restricted reports stay administrator-only in both DRS and Superset.
- **Row filters**: Embedded mode in Superset currently does not support row filters. Reports with
  row filters cannot have dashboards attached (the BI page displays the refusal).

### PostgreSQL only
Self-service is supported on **PostgreSQL** DRS databases only (which provide schema views in
`drs_bi`). On SQLite installations, the BI page indicates that self-service is not available on SQLite.

## Link mode (any BI tool)

`bi_design_uri = 'https://...'` shows that address in the Dashboard tab with an "Open in a new
tab" button. The user signs in to the BI tool; permissions on the data are the tool's own. A report
with row-filter rules cannot use link mode.

## What a dataset is

A dashboard is one more view of a report's result, beside the grid and the HTML design: it shows
the result **for the parameters the user chose**, and it changes when they choose others and run
again. Two users with different parameters see different data in the same dashboard at the same
moment.

DRS keeps every run of a report as a snapshot - the JSON of its rows, for one set of parameter
values, for the report's retention. The dataset is that JSON read as rows and columns:

- **PostgreSQL**: `drs_bi.<name>` is a **view** over `drs_report_snapshot` - SQL that unpacks the
  JSON into typed columns (`pg_get_viewdef('drs_bi.<name>')` shows it). Nothing is copied, nothing
  has to be refreshed, and a snapshot leaves the dataset when it leaves the cache. DRS creates the
  view, and replaces it when the report's columns change.
- **SQLite**: a BI tool is never given the DRS database file, so the snapshots that are asked for
  are written to the table `<name>` of `runtime/bi/datasets.sqlite3`, and leave it with their
  snapshot.

Both have the column `drs_snapshot_id`. When a user opens the Dashboard tab, DRS asks Superset for
a guest token that carries the row-level rule `drs_snapshot_id = <the snapshot on their screen>` -
Superset's own mechanism for embedded dashboards - so the charts read that one result. The rule
applies to every dataset of the dashboard: build a report's dashboard on that report's dataset.

In Superset itself, outside DRS, no such rule applies: the dataset shows every result of the report
that is in the cache at that moment. While designing, keep one (`python -m drscore cache clear
--report CODE`, then run the report once), or filter on `drs_snapshot_id` without saving that filter
into the chart. A filter saved inside a dashboard (Superset's own filter bar) keeps working: it
narrows what the user's parameters returned.

Superset can also save a dashboard as PDF or image (Download menu).

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `superset-init` stops at `User last name [user]: Aborted!`, then `--lastname: command not found` | an old `docker/compose.yml`: the init command was a folded YAML scalar that broke the `create-admin` line in two. Fixed in the file (a literal block). |
| The Superset container restarts for ever: `'127.0.0.1' is not a valid port number` | `SUPERSET_PORT` holds an address. It is read by the image too: keep a number there and put the address in `SUPERSET_BIND`. |
| An API POST, or DRS's guest token, is refused: `The CSRF session token is missing` (DRS shows `BI_ENGINE_UNAVAILABLE ... guest_token: HTTP 400`) | Superset is served over plain http while its session cookie is marked `Secure`, so the client never sends it back. `"session_cookie_secure": False` in `TALISMAN_CONFIG` (set it True again together with `force_https`). |
| The Dashboard tab shows `403 Forbidden: You don't have the permission to access the requested resource` | Superset compares the Referer of the frame with the dashboard's "allowed domains". They must hold the portal's address exactly (`http://localhost:8090`, no path). The portal sends its origin for that frame only (`referrerPolicy` in `report.js`); a browser extension that strips the Referer breaks it. |
| A container cannot reach a server of the office network whose address starts with `172.17.` (time-out) | Docker's default bridge network is `172.17.0.0/16`: inside Docker such an address never leaves the machine. Move Docker off that range (Docker Engine settings: `"bip": "10.213.0.1/24"`, then restart Docker), or - without touching Docker - run a TCP relay on the host and name `host.docker.internal:<port>` in Superset's connection. |
| The dashboard shows old numbers | The dataset table follows the report's retention: it is rewritten when the Dashboard tab is opened after `cache_ttl_seconds`, or by `python -m drscore cache warm --report CODE`. |
| DRS shows `BI_ENGINE_UNAVAILABLE: the Superset admin password is not set` | The environment variable configured in `admin_password_env` (`DRS_SUPERSET_ADMIN_PASSWORD`) is not exported in the DRS process environment. |
| The BI page says `dataset not made yet - run the report once` | The report has not been executed yet so its view in `drs_bi` does not exist. Open the report and click **Run**, or click **Sync now** on the BI page after running. |
| Attaching a dashboard is refused: `Dashboard uses N other dataset(s)` | All charts in the dashboard must use only the report's dataset. Embedded guest tokens enforce `drs_snapshot_id` across every dataset in the dashboard. |
| Attaching a dashboard is refused: row filters | Reports with row filters (`drs_report_row_filter`) cannot be embedded because guest tokens do not carry row rules for row filters. Remove the row filters or use grid/HTML views. |
| The BI page says `not available on SQLite` | Self-service Superset integration is available on PostgreSQL DRS databases only (which provide schema views in `drs_bi`). |
