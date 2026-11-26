# DRS 1.6.0 - new installation on Linux (IT / DevOps)

A new, empty DRS: two containers, **drscore** (portal, port 8090) and **drscore-superset**
(dashboards, port 8088), from one `docker-compose.yml`. All data is in a PostgreSQL database; no
internet is needed on the server.

## 1. Files

Folder **`dist/drs-1.6.0/`** of the drscore repository (built files, not in git; the DRS owner
sends them):

| File | Size | Content |
| --- | ---: | --- |
| `drscore-msodbc-1.6.0.image.tar.gz` | 104.4 MB | image of drscore (with the SQL Server ODBC driver) |
| `drscore-superset-6.1.0.image.tar.gz` | 326.1 MB | image of Apache Superset 6.1.0 |
| `drs-deploy-1.6.0.tar.gz` | 7.3 KB | the installation folder `drs/`, files below |
| `SHA256SUMS` | 292 B | checksums of the three files |

Inside `drs-deploy-1.6.0.tar.gz` (unpacked into `/opt/drs/` in step 5):

| File | Size | Content |
| --- | ---: | --- |
| `docker-compose.yml` | 2.9 KB | containers `drscore`, `superset`, and `superset-init` (run once) |
| `.env.example` | 1.2 KB | template of `.env`: drscore ports, uid/gid, passwords, key |
| `superset.env.example` | 1.2 KB | template of `superset.env`: Superset keys, admin, database, **portal URL** |
| `config/app.toml` | 3.3 KB | DRS settings, **portal and Superset URLs** |
| `config/database.toml` | 0.8 KB | DRS database connection |
| `config/openssl-legacy.cnf` | 0.4 KB | lets the SQL Server driver reach old SQL Servers (SHA-1 TLS) |
| `superset/superset_config.py` | 5.7 KB | Superset settings, read from `superset.env` (nothing to edit) |
| `postgres/create_drs_database.sql` | 1.8 KB | database and logins (step 3) |
| `designs/html/`, `runtime/` | - | empty folders: HTML designs, logs |

No password or key is in these files: IT creates them below and keeps them in the password safe.

## 2. Requirements

- Linux x86-64 (Ubuntu 22.04+ / Debian 12+ / RHEL 9+), 4 CPU, 8 GB RAM, 20 GB disk.
- Docker Engine 24+ with Compose v2; a user in group `docker` owning `/opt/drs`.
- PostgreSQL 14+ (this server or another).
- Open: users -> server **8090** and **8088**; server -> PostgreSQL 5432; server -> report sources
  (list from the DRS administrator).

## 3. PostgreSQL (once, as superuser)

```bash
tar -xzf drs-deploy-1.6.0.tar.gz drs/postgres/create_drs_database.sql
psql -h <pg-host> -U postgres -v app_drs_pw='<pw1>' -v dataset_pw='<pw2>' -v mirror_pw='<pw3>' \
     -f drs/postgres/create_drs_database.sql
```

Creates database `drs` (schemas `drs`, `drs_bi`, `drs_mirror`, `superset`) and logins `app_drs`
(owner, used by both containers), `app_drs_dataset` (Superset reads `drs_bi`), `drs_mirror_ro`.
In `pg_hba.conf` allow the DRS server (and the Docker range of step 4 if PostgreSQL is on the same
host): `host drs app_drs,app_drs_dataset,drs_mirror_ro <drs-server-ip>/32 scram-sha-256`.

## 4. Docker address ranges (before the first container)

Docker's default `172.17-172.19.x` ranges hide company servers with such addresses. In
`/etc/docker/daemon.json` (ranges the company network does not use), then `sudo systemctl restart docker`:

```json
{ "bip": "172.30.0.1/24", "default-address-pools": [ { "base": "172.31.0.0/16", "size": 24 } ] }
```

## 5. Install

```bash
sha256sum -c SHA256SUMS
docker load -i drscore-msodbc-1.6.0.image.tar.gz
docker load -i drscore-superset-6.1.0.image.tar.gz
sudo mkdir -p /opt/drs && sudo chown "$(id -u):$(id -g)" /opt/drs
tar -xzf drs-deploy-1.6.0.tar.gz -C /opt/drs --strip-components=1
cd /opt/drs && cp .env.example .env && cp superset.env.example superset.env && chmod 600 .env superset.env
```

## 6. Configure

`<drs-host>` = address users open; `<pg-host>` = PostgreSQL as the containers reach it (not `localhost`).

**URLs** - no command sets them: they are text in `config/app.toml`, `config/database.toml` and
`superset.env`, as the placeholders `<drs-host>` / `<pg-host>`. Fill all at once:

```bash
cd /opt/drs
sed -i 's|<drs-host>|drs.company.local|g; s|<pg-host>|10.0.0.20|g' config/app.toml config/database.toml superset.env
grep -n 'base_url\|host =\|DB_HOST\|PORTAL_ORIGIN' config/*.toml superset.env   # check
```

| URL | Where | Key |
| --- | --- | --- |
| Portal `http://<drs-host>:8090` | `config/app.toml` | `[app] base_url` |
| Portal (frame permission) | `superset.env` | `DRS_PORTAL_ORIGIN` (same value, no trailing `/`; several, comma-separated) |
| Superset `http://<drs-host>:8088` | `config/app.toml` | `[bi.superset] base_url` |
| PostgreSQL | `config/database.toml`, `superset.env` | `host`, `SUPERSET_DB_HOST` |
| Ports 8090 / 8088 | `.env` | `DRS_PORT` (= `[app] port`), `SUPERSET_PORT` |

After a change: `docker compose up -d` (and `docker compose restart drscore` for `config/`).

**A domain (reverse proxy) beside the IP address.** DRS and Superset can be opened at several
addresses at once. Nothing in the code names one; each is a setting:

| What | Where | Key |
| --- | --- | --- |
| The portal's other address, e.g. `https://portal.example.com` | `config/app.toml` | `[app] other_urls = ["https://portal.example.com"]` |
| The proxy's IP (real client IP in the logs) | `config/app.toml` | `[app] forwarded_allow_ips = "127.0.0.1,<proxy-ip>"` |
| Superset may be framed from it too | `superset.env` | `DRS_PORTAL_ORIGIN=http://<drs-host>:8090,https://portal.example.com` |
| Superset's https address for that page, e.g. `https://bi.example.com` | `config/app.toml` | `[bi.superset] site_urls = { "https://portal.example.com" = "https://bi.example.com" }` |
| Superset behind the proxy | `superset.env` | `SUPERSET_PROXY_FIX=1` |

The proxy must pass the `Host` header on (nginx: `proxy_set_header Host $host;` plus
`X-Forwarded-Proto $scheme`). A page opened at an https address can frame only an https Superset
(the browser blocks http inside https): until Superset has its own https address, dashboards show
"The BI system cannot be reached" there and work at the http address. Keep `[session]
cookie_secure = false` while an http address is in use. After changing these: `docker compose up -d`
(both containers), then `docker compose exec drscore python -m drscore bi sync` - it lets every
portal address embed the dashboards already attached.

| File | Set |
| --- | --- |
| `.env` | `DRS_UID` / `DRS_GID` = `id -u` / `id -g`; `DRS_DB_PASSWORD` = pw1; `DRS_SECRET_KEY` = output of `docker run --rm drscore-msodbc:1.6.0 python -m drscore secret new-key`; `DRS_SUPERSET_PASSWORD` = a new password (step 8) |
| `superset.env` | `SUPERSET_SECRET_KEY`, `SUPERSET_GUEST_TOKEN_SECRET` = `openssl rand -base64 42` each; `SUPERSET_DB_PASSWORD` = pw1 |

`DRS_SECRET_KEY` and `SUPERSET_SECRET_KEY` must never change or be lost.

The AI agent's key and the Telegram / Zalo bot tokens (1.7.0, `docs/feedback-typhon-notices.md`) do not go in
`.env`: store each encrypted in the DRS database, from the running container, without it ever being shown -
`docker compose exec -T drscore python -m drscore secret set ai.api_key --stdin < <file holding one line>`, then
delete that file - and set `[ai] enabled = true` / the groups' `chat_id` in `config/app.toml`. Since 1.7.0 the
image also needs the `anthropic` package and its dependencies: build it with network access to PyPI, or
install the wheels of `pip download drscore==1.7.0` with it.

## 7. Start

```bash
docker compose --profile init run --rm superset-init   # once: Superset tables + admin
docker compose up -d
curl -s localhost:8090/healthz                         # {"status": "ok", ...}
curl -s localhost:8088/health                          # OK
```

Open `http://<drs-host>:8090` at once, sign in `admin` / `admin`, set a new password.

## 8. Superset, once (`http://<drs-host>:8088`)

1. Sign in `admin` / `admin`, set a new password; put it in `.env` as `DRS_SUPERSET_ADMIN_PASSWORD`.
2. Settings > Database Connections > + PostgreSQL, name **`DRS`**:
   `postgresql+psycopg2://app_drs_dataset:<pw2>@<pg-host>:5432/drs`.
3. Settings > List Roles: copy `Gamma` as **`DRS_Embedded`**; new role **`DRS_Service`** with
   `can grant guest token on SecurityRestApi`, `can read on SecurityRestApi`,
   `can read on EmbeddedDashboard`, `can read on Dashboard`.
4. Settings > List Users: **`drs_service`**, role `DRS_Service`, password = `DRS_SUPERSET_PASSWORD`.
5. `docker compose up -d drscore` (reads the new `.env`).

Hand back to the DRS administrator: both addresses, both admin accounts, pw3 (`drs_mirror_ro`).

## 9. Operate

- `docker compose ps` / `logs --tail 200 drscore` / `restart drscore` (after `config/` changes).
  Request log: `runtime/logs/drs.log`. Both restart with the server.
- Backup: nightly `pg_dump -Fc drs` (DRS and Superset), plus `config/`, `designs/`, `.env`,
  `superset.env`, the two keys.
- HTML designs: copied by the DRS administrator into `/opt/drs/designs/html/` (no restart).
- Upgrade: `pg_dump`, `docker load` the new image, `DRSCORE_VERSION=<v>` in `.env`, `docker compose up -d drscore`.

| Problem | Fix |
| --- | --- |
| drscore cannot reach PostgreSQL | `<pg-host>` must not be `localhost`; firewall; `pg_hba.conf`; step 4 |
| `runtime/` permission denied | `DRS_UID` / `DRS_GID` = owner of `/opt/drs` |
| Dashboard in the portal: `403 Forbidden` | `DRS_PORTAL_ORIGIN` exactly the portal address(es) (no trailing `/`), then `bi sync` |
| Admin pages without styles at a domain | `[app] other_urls` names that https address |
| `BI_ENGINE_UNAVAILABLE` | `DRS_SUPERSET_*` passwords in `.env`, then `docker compose up -d drscore` |
