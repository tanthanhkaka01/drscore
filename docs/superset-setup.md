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

Schema `drs_bi` exists after the first dataset was loaded (`python -m drs cache warm --report CODE`
for a report with `bi_dataset_table`). The connection of step 3.1 then names that server; a
password with special characters is URL-encoded in the address (`#` is `%23`).

## 2. Point DRS at that PostgreSQL

`config/database.toml`: `active = "postgresql"`, `database = "drs"`, `user = "drs_app"`,
`schema = "drs"`, `bi_schema = "drs_bi"`, password in `DRS_DB_PASSWORD`. Then
`python -m drs db upgrade`.

## 3. In Superset (as admin, http://localhost:8088)

1. **Database connection** (Settings > Database Connections > + Database > PostgreSQL):
   `postgresql+psycopg2://superset_reader:<SUPERSET_READER_PASSWORD>@postgres:5432/drs`.
   Use this login only - never `drs_app`.
2. **Datasets**: Datasets > + Dataset > schema `drs_bi` > the table named in
   `drs_report.bi_dataset_table` of the report. The table exists after the report has run once
   (open its Dashboard tab, or `python -m drs cache warm --report CODE`).
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

## Link mode (any BI tool)

`bi_design_uri = 'https://...'` shows that address in the Dashboard tab with an "Open in a new
tab" button. The user signs in to the BI tool; permissions on the data are the tool's own. A report
with row-filter rules cannot use link mode.

## Keeping the data fresh

The dataset table is rewritten from the report's snapshot, following the report's retention
(`cache_ttl_seconds`): when someone opens the Dashboard tab after the retention, or when
`python -m drs cache warm` runs (schedule it to keep dashboards fresh without anyone opening
them). The table is replaced in one transaction, so Superset never sees it empty.

Superset can also save a dashboard as PDF or image (Download menu).

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `superset-init` stops at `User last name [user]: Aborted!`, then `--lastname: command not found` | an old `docker/compose.yml`: the init command was a folded YAML scalar that broke the `create-admin` line in two. Fixed in the file (a literal block). |
| The Superset container restarts for ever: `'127.0.0.1' is not a valid port number` | `SUPERSET_PORT` holds an address. It is read by the image too: keep a number there and put the address in `SUPERSET_BIND`. |
| An API POST, or DRS's guest token, is refused: `The CSRF session token is missing` (DRS shows `BI_ENGINE_UNAVAILABLE ... guest_token: HTTP 400`) | Superset is served over plain http while its session cookie is marked `Secure`, so the client never sends it back. `"session_cookie_secure": False` in `TALISMAN_CONFIG` (set it True again together with `force_https`). |
| The Dashboard tab shows `403 Forbidden: You don't have the permission to access the requested resource` | Superset compares the Referer of the frame with the dashboard's "allowed domains". They must hold the portal's address exactly (`http://localhost:8090`, no path). The portal sends its origin for that frame only (`referrerPolicy` in `report.js`); a browser extension that strips the Referer breaks it. |
| A container cannot reach a server of the office network whose address starts with `172.17.` (time-out) | Docker's default bridge network is `172.17.0.0/16`: inside Docker such an address never leaves the machine. Move Docker off that range (Docker Engine settings: `"bip": "10.213.0.1/24"`, then restart Docker), or - without touching Docker - run a TCP relay on the host and name `host.docker.internal:<port>` in Superset's connection. |
| The dashboard shows old numbers | The dataset table follows the report's retention: it is rewritten when the Dashboard tab is opened after `cache_ttl_seconds`, or by `python -m drs cache warm --report CODE`. |
