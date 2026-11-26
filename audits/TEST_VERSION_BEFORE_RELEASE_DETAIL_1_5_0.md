# 1.5.0 - work list

**Opened 2026-10-22, at the operator's word.** The procedure is
[`TEST_VERSION_BEFORE_RELEASE.md`](./TEST_VERSION_BEFORE_RELEASE.md).

**Why this version exists:** a group dashboard for the president of an apparel company, read from
two ERP servers and four factory databases, shown on a desk screen and a wall TV without a login.
The operator, 2026-10-22 (translated): *heavy queries straight on the ERP hurt it - clone the data
into PostgreSQL, table by table, automatically, reusable for many reports, and run the heavy queries
there*; and, from 2026-10-21, *the report opens from its own link with a token, pasted into a browser,
no login*.

---

## 0. Where 1.5.0 stands

| Item | State |
| --- | --- |
| 1.1 | mirrors: copies of source tables in the DRS database (migration 0013) - **done** |
| 1.2 | public links: a report opened by a secret URL, without signing in (migration 0014) - **done** |
| Tests | full suite on SQLite and PostgreSQL: 754 passed, 2 failed (also failing on 1.4.2) - see §2 |
| Build | wheel from the LF export of `720af1e` - **done** (a first build of `79e300c` was replaced, §3) |
| Worker | `drscore-msodbc:1.5.0` running since 2026-10-22T07:51:05Z, `/healthz` ok - **done** |
| Check | 30 mirrors copied by the worker, a report on the copies, a dashboard opened by a public link - **done** |
| Publish | not yet: waits for the operator's word |

---

## 1. Items

### 1.1 Mirrors - **asked 2026-10-22, done the same day**

**What was asked.** A report over a production ERP ran its joins and aggregations on the ERP every
time a viewer opened it. The copies must be made one table at a time, on their own, and serve every
report that needs the same table.

**What was built.** `drscore.mirror` and `python -m drscore mirror add|set|list|show|run|import|export`.

- A mirror copies one source table or view (any source type DRS reads) into the mirror schema of the
  DRS database (`[postgresql] mirror_schema`, default `drs_mirror`; SQLite: `[sqlite] mirror_path`).
  Definitions are rows of `drs_mirror_table` (migration 0013); changes are audited (`MIRROR_ADD`,
  `MIRROR_SET`).
- **full**: the rows that pass the mirror's filter replace the mirror's own rows. **incremental**:
  the rows whose watermark column (a modification time, a growing id) is above the last value read,
  minus a look-back, are inserted or updated by key; a date watermark re-reads its last day; a
  periodic full copy (`full_refresh_hours`) at chosen hours (`full_hours`) removes rows deleted at
  the source. A changed definition (source, filter, columns, key, watermark) starts with a full copy.
- Several sources write one target table: every row carries `_source` (the datasource code) and a
  full copy replaces only its own rows. Target tables are created at the first run; a new column is
  added, a column is never dropped.
- The source is read in one read-only transaction, rolled back, connection back in autocommit (as a
  report run); `read_uncommitted` for a SQL Server source without read-committed snapshot - the
  isolation level is set back before the connection returns to the pool reports use. The target is
  written in one transaction: COPY into a temporary table, then one merge (one row per key, the
  newest by watermark); ANALYZE after a full copy.
- `serve` runs the due mirrors in a background thread when `[mirror] enabled = true`; a run is
  claimed in the DRS database for 15 minutes, renewed every minute while it copies, so a server
  stopped mid-copy frees its mirror soon and two processes never copy the same table at once.
- `[mirror] read_role`: the PostgreSQL role given USAGE on the schema and SELECT on every mirror
  table - the login of the datasource that reports read the copies through.

**Migration:** 0013 (`drs_mirror_table`). Rollback to 1.4.2 leaves the table and the mirror schema in
place, unused.

### 1.2 Public links - **asked 2026-10-21, done 2026-10-22**

**What was built.** `drscore.publiclink`, the routes `GET /p/<token>` and `GET /p/<token>/bi-token`,
and `python -m drscore publiclink add|list|show|revoke`.

- A link opens one report as one chosen DRS user (`--as`): that user's grants and row filters are
  checked at every use; disabling the user or the report, or withdrawing the grant, closes the link.
- The dashboard is shown for the snapshot of the link's own run: the token route runs (or reads) the
  report itself and never takes a snapshot from the browser. Parameter values are fixed by the link;
  the parameters listed as editable may be changed by the reader and are validated as any input.
- Only the SHA-256 of the token is stored (`drs_public_link`, migration 0014); the URL is printed
  once. A link is switched off (`revoke`), never deleted; making and revoking are audited
  (`PUBLICLINK_ADD`, `PUBLICLINK_REVOKE`); every view is in the access log as `public:<token start>`.
- Every unusable link answers the same 404, and so does every `/p/` address while
  `[public] enabled = false` (the default). Pages are sent `no-store`, `noindex`, `no-referrer`.
- The page: no menu, no session; dark by default (`[public] theme`); `[public] locale` fixes its
  language; `--refresh` reloads it (a wall screen).

**Migration:** 0014 (`drs_public_link`).

### Review

An independent review of the diff (another model, read-only) listed 12 findings; each was checked
against the code. Nine were real and are fixed above (isolation level left on a pooled connection,
a withdrawn grant answering 200, a missing watermark column crashing, a date watermark skipping
same-day rows, a schema creation race, a new column keeping the old watermark, an empty full copy
keeping its watermark, a 4-hour claim after a stop, `mirror run` stopping at the first unexpected
error). One was not a defect (float and Decimal compare in Python); one is documented (on SQLite a
reader waits during a copy).

---

## 2. Tests

| Run | When (UTC) | Result |
| --- | --- | --- |
| `tests/test_mirror.py` (14), `tests/test_publiclink.py` (9), `test_db.py`, `test_cli.py` - SQLite and PostgreSQL 16 | 2026-10-22 | all passed |
| Full suite on `720af1e`, SQLite and PostgreSQL 16 (a local container), symlink test deselected | 2026-10-22T07:45Z | 754 passed, 3 skipped, **2 failed** |

**The two failures are not this version's:** `test_bi_members.py::test_the_bi_page_saves_the_form[postgresql]`
and `test_selfservice.py::test_sync_report_on_sqlite_returns_not_available[postgresql]` fail the same
way on 1.4.2 (checked on a clean worktree of `656f0d9`'s line): earlier sheets ran SQLite only. The
symlink test is deselected on the Windows build PC (`WinError 1314`, see the 1.4.2 sheet).

**Rehearsal before the deploy:** the mirror definitions of the dashboard (30 tables of two ERP and
four factory servers) were run from the build PC into a throwaway local PostgreSQL: about 3.0M rows
in 4 min 14 s, no error; an incremental run of the invoice lines read 989 changed rows in 0.7 s.

---

## 3. Build and deploy

| Step | When (UTC) | Result |
| --- | --- | --- |
| Commit `Release 1.5.0` (first) | 07:18:57Z | `79e300c` |
| Wheel from `git -c core.autocrlf=false archive 79e300c` | 07:19Z | 3 011 007 bytes, sha256 `63e48b36698e9c6268018a742cedb65f323564e8fb58cec9f026a200e689fcce` |
| `drscore:1.5.0` / `drscore-msodbc:1.5.0` | 07:20:28Z / 07:20:57Z | `3413cb8108b4` / `00ee6965da69` |
| `.env` -> `.env.bak-1.4.2`, `config/app.toml` -> `app.toml.bak-1.4.2`; `[mirror]`, `[public]` added; `DRSCORE_VERSION=1.5.0` | 07:21Z | - |
| `docker compose up -d drscore` | 07:21:17Z | migrated 0012 -> 0014, `/healthz` ok |
| Commit amended (not public): `[public] locale`, refresh in whole minutes | 07:49Z | `720af1e` |
| Wheel from `git -c core.autocrlf=false archive 720af1e` | 07:49:09Z | 3 011 117 bytes, sha256 `e875ab52d1060849ba62d55da8c896012283c7b2c77e50526e6347e3db4cec3c`; the first wheel kept as `drscore-1.5.0-py3-none-any.whl.79e300c` |
| `drscore:1.5.0` / `drscore-msodbc:1.5.0` rebuilt | 07:51:03Z | `52fe721a5366` / `068f0c2c1e39` (the first images stay, untagged) |
| `[public] locale = "en"` on the worker; `docker compose up -d drscore` | 07:51:05Z | `/healthz` `{"status":"ok","database":"postgresql"}` |

**Kept for rollback:** the 1.4.2 (and older) images and wheels, `.env.bak-1.4.2`,
`config/app.toml.bak-1.4.2`. Nothing removed. Rollback leaves migrations 0013 / 0014 in place
(two new tables, unused by 1.4.2).

**Check on the worker (D):**
- A read-only PostgreSQL role for the mirror datasource was created (login, `default_transaction_read_only`,
  CONNECT on the DRS database; SELECT on the mirror schema granted by DRS).
- 30 mirror definitions imported; the worker's own scheduler copied all 30 tables in about 2.5 min,
  no error; hourly incremental runs since.
- A report on the mirror (17 661 rows) runs in 23 s through the portal as a designer; its Superset
  dataset was made by self-service and a dashboard of 18 charts linked to it.
- A public link to that report, opened anonymously: page 200 in 0.35 s, `no-store` / `noindex` /
  `no-referrer`; guest token 200; a wrong token 404; rendered in headless Edge at 1920x1080 - every
  chart drawn, dark theme, English; the editable currency parameter switches the amounts.

---

## 4. Publish

Not done: waits for the operator's word (`main` push, tag `v1.5.0`).
