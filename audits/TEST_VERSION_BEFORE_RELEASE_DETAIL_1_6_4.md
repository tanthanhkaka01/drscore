# 1.6.4 - work list

**Opened 2026-11-09, at the operator's word.** The procedure is
[`TEST_VERSION_BEFORE_RELEASE.md`](./TEST_VERSION_BEFORE_RELEASE.md). Built from the private repository.

**Why this version exists:** the portal got a second address, a domain served over https by a reverse proxy, beside
its IP address over http. At the domain the admin pages lost every style and script: behind the proxy DRS took the
request for http and the admin pages (sqladmin) write absolute links, which a browser blocks inside an https page.
Superset could be framed by one portal address only, and the embedded dashboards allowed one address only.

---

## 0. Where 1.6.4 stands

| Item | State |
| --- | --- |
| 1.1 | `[app] other_urls`: more portal addresses; a request is served with the scheme of the address it came in at - **done** |
| 1.2 | `[bi.superset] site_urls`: Superset's browser address per portal address; mixed content reported - **done** |
| 1.3 | Embedding allowed domains = every portal address; `bi sync` re-applies them - **done** |
| 1.4 | Superset config: several portal origins, `SUPERSET_PROXY_FIX` - **done** |
| Tests | SQLite full suite: 469 passed, 2 failed (§2); PostgreSQL run stopped by the build PC (memory) - **not run** |
| Build | wheel from an LF export of the private working tree (git tree `82881bbc`, **not a commit**) |
| Worker | `drscore-msodbc:1.6.4` running since 2026-11-09T02:52Z, no migration - **done** |
| Check | sign-in and pages at the domain and the IP (§3); admin pages at the domain: to be seen by an administrator |
| Commit / publish | not yet: waits for the operator's word |

---

## 1. Items

### 1.1 Several portal addresses

- `[app] other_urls = ["https://..."]` (checked: scheme://host[:port], no path). `siteurl.SiteSchemeMiddleware`
  serves a request whose Host is one of the portal's addresses with that address's scheme, so absolute links
  (sqladmin) are right whether or not the proxy sends X-Forwarded-Proto. `[app] base_url` is now checked the same way.
- The public-link origin check accepts every portal address; `publiclink add` prints the link at each address.
- Start-up warnings: `cookie_secure = true` with an http address; `site_urls` naming an unknown portal address; an
  https portal address whose Superset is http.

### 1.2 Superset's address per portal address

- `[bi.superset] site_urls = { "<portal>" = "<superset>" }`, else `base_url`. Used by the embedded dashboard, the
  page's `frame-src` and the self-service Explore / dashboard links.
- An https page with an http Superset: the BI tab says `BI_ENGINE_UNAVAILABLE` (administrators see why) instead of an
  empty frame.

### 1.3 Embedding allowed domains

- Linking a dashboard allows every portal address. `bi sync` (all, or `--report`) re-applies them to dashboards already
  attached (`selfservice.sync_embedding`).

### 1.4 Superset configuration (`docker/superset/superset_config.py`)

- `DRS_PORTAL_ORIGIN` holds several addresses, comma-separated -> `frame-ancestors`.
- `ENABLE_PROXY_FIX` from `SUPERSET_PROXY_FIX` (off by default).
- docs: `docs/deployment.md` (a domain beside the IP), `config/app.example.toml`, `docker/.env.example`.

---

## 2. Tests

| Run | When | Result |
| --- | --- | --- |
| `tests/test_siteurl.py`, 6 new (settings checks, choice of address, admin links at the domain over a plain-http hop, admin form post at the domain, unknown host left alone) | 2026-11-09 | 6 passed |
| Full suite, SQLite | 2026-11-09 ~02:35Z | 469 passed, 6 skipped, 2 failed |
| `test_cli`, `test_siteurl`, `test_startup`, `test_settings` after the fix below | 2026-11-09 | 19 passed |
| Full suite, SQLite + PostgreSQL 16 | 2026-11-09 | stopped by Claude Code: the build PC ran low on memory - **not run** |

Failures: `test_designs::test_a10_symlink_out_of_the_root_is_invalid` (Windows symlink privilege, as before) and
`test_cli::test_init_writes_the_example_configuration_and_never_overwrites` (the packaged `drscore/examples/app.example.toml`
was not updated with the source copy: fixed, then passed).

---

## 3. Build and deploy

| Step | When (UTC) | Result |
| --- | --- | --- |
| Wheel from tree `82881bbc` | 2026-11-09 ~02:45Z | 3 034 435 bytes, sha256 `468d8d8548af9e924cbad460ac608d255d309008aa843021d807b23859e6aeaf` |
| Backups `config/app.toml`, `superset.env`, `superset/superset_config.py`, `.env` -> `*.bak-1.6.3` | 02:50:50Z | done |
| `pg_dump -Fc` of the DRS database, before the upgrade | 02:50-02:51Z | `runtime/pre_upgrade_1.6.4/drs.dump`, 216 025 444 bytes |
| `drscore-msodbc:1.6.4` with `image/Dockerfile.swap` on `drscore-msodbc:1.6.3` (disk 98 % used: no full rebuild) | 02:51Z | `5e517149138c` |
| `app.toml`: `other_urls = ["https://portal.example.com"]`, `forwarded_allow_ips = "127.0.0.1,<proxy>"` | 02:51Z | done |
| `superset.env`: `DRS_PORTAL_ORIGIN=http://<worker>:8090,https://portal.example.com`; new `superset_config.py` | 02:51Z | `frame-ancestors 'self' http://<worker>:8090 https://portal.example.com` |
| `DRSCORE_VERSION=1.6.4`; `docker compose up -d drscore superset` | 02:52Z | no migration, `/healthz` ok, Superset `/health` OK |
| Embedding allowed domains re-applied | 02:52Z | 7 dashboards (1, 2, 3, 6, 8, 10, 11) allow both addresses |

Start-up warning, expected: the domain is https and Superset only http (no https address for Superset yet).

**Kept for rollback:** the 1.6.3 image and wheel, the four `*.bak-1.6.3` files. Rollback = copy them back,
`docker compose up -d drscore superset` (no migration between them).

**Check on the worker:** at `https://portal.example.com` and `http://<worker>:8090`, a designer signs
in, `/` and `/design` answer 200, the request log shows the client's own IP (the proxy is trusted).

---

## 4. Publish

Not published. Before a public release: commit in the private repository, push to the public one, check that nothing
internal is in it, tag `v1.6.4` there.
