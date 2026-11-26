# 1.5.1 - work list

**Opened 2026-10-22, at the operator's word.** The procedure is
[`TEST_VERSION_BEFORE_RELEASE.md`](./TEST_VERSION_BEFORE_RELEASE.md).

**Why this version exists:** the president's dashboard of 1.5.0 (a Superset dashboard behind a public
link) was asked to do what an embedded dashboard cannot: filters and currency in one row, the
currency switched without reading the database again, titles that follow the filters ("Trax Group
revenue", "VIETNAM revenue", "Company 2 revenue"), units on every number, a phone layout, and no error or
wait while it reloads its data. The operator, 2026-10-22 (translated): *the data should be loaded
ahead and shown fast; keep the view as it is and load the data quietly behind it, then swap it in.*
The reload error itself was a 410 `SNAPSHOT_GONE` on the guest token after the source failed
(PostgreSQL `/dev/shm` full), which the page answered by wiping the dashboard.

---

## 0. Where 1.5.1 stands

| Item | State |
| --- | --- |
| 1.1 | public links show an HTML design, drawn at once and refreshed behind the reader (migration 0015) - **done** |
| 1.2 | `publiclink set`: change what a link shows, same address - **done** |
| 1.3 | an embedded dashboard is no longer wiped when a guest token cannot be renewed - **done** |
| Review | Gemini (`agy`, read-only): 10 findings, 3 real and fixed, 1 test added (§2) |
| Tests | full suite on SQLite and PostgreSQL: 762 passed, 4 failed - the same 4 as 1.5.0 (§2) |
| Build | wheel from the LF export of `0edb7a5` - **done** |
| Worker | `drscore-msodbc:1.5.1` running since 2026-10-22T09:45Z, migrated 0014 -> 0015 - **done** |
| Check | the president's link shows the HTML dashboard, anonymously, at four screen sizes; the background swap seen working - **done** |
| Publish | not yet: waits for the operator's word (1.5.0 is not published either) |

---

## 1. Items

### 1.1 Public links with an HTML design

- `drs_public_link.view_key` takes `html` (migration 0015: the CHECK constraint widened; SQLite
  rebuilds the table keeping AUTOINCREMENT; the downgrade switches `html` links off).
- `GET /p/<token>` answers at once with a frame and runs nothing. The frame loads
  `GET /p/<token>/html?cached=1`: the design drawn from the newest snapshot the link has, live or in
  the history, even out of its retention (`views.newest_snapshot`, the same permission, view and
  parameter checks as a run). `GET /p/<token>/html` runs the report when the cache has nothing
  current. Both answer with the design's nonce CSP plus `no-store` / `noindex` / `no-referrer`; a
  failure answers DRS's page marked `data-drs-error`.
- `public.js`: when the shown snapshot is out of its retention, and then every `--refresh` seconds,
  a fresh copy loads in a hidden frame and replaces the shown one once loaded, keeping the design's
  `#hash` (its filter state) and the scroll position; a failed copy is dropped; a copy that hangs
  10 minutes is given up so the refresh goes on.
- The bar above the frame appears only for editable parameters: the design draws its own title,
  filters and "data as of".

### 1.2 `publiclink set`

`python -m drscore publiclink set REF [--report] [--as] [--param ... | --no-params] [--editable]
[--view] [--title] [--refresh] [--expires]`: options not given are kept, the link is checked again
as a whole as `add` checks it, audited `PUBLICLINK_SET`; a switched-off link cannot be changed; an
expiry kept as it was may have passed (the link stays closed).

### 1.3 Embedded dashboard

`fetchGuestToken` keeps the last token when a renewal fails and asks again a minute later, instead of
replacing the dashboard with "could not be loaded". Known limit, documented: a snapshot served only
because the source failed (`serve_stale_on_error`) lives in the history, which the dataset view does
not read - such a dashboard has no rows.

---

## 2. Review and tests

**Gemini review** of the diff (`gemini-3.8-flash-medium`, plan mode; the `flash-high` run ended in
`ERROR` with no text). 10 findings, each checked against the code:

| # | Finding | Verdict |
| --- | --- | --- |
| 1 | `_checked` does not validate params | wrong - it does (`unknown` -> `LinkError`) |
| 2 | `params_for` raises outside the `try` | wrong - it only merges dicts; validation is in `prepare`, inside the `try` |
| 3 | `set` on an expired link fails without `--expires` | **real** - fixed: a kept expiry is not checked |
| 4 | a hung background frame stops the refresh for good | **real** - fixed: given up after 10 minutes |
| 5 | the error page cannot be framed | wrong - same-origin framing is allowed |
| 6 | a second source run on the first open | wrong - the second request is a cache hit |
| 7 | returning an expired token | by design (keeps the dashboard, retries every minute) |
| 8 | the downgrade is untested | fair - test added (down to 0014 and up again, both engines) |
| 9 | detached link object | wrong - only loaded columns are read |
| 10 | the scroll restore may lose to the hash change | hardened - restored again after a tick |

| Run | When | Result |
| --- | --- | --- |
| `tests/test_publiclink.py` (14), SQLite and PostgreSQL 16 | 2026-10-22 | 28 passed |
| Full suite on the working tree committed as `6829b2f`, SQLite and PostgreSQL 16 | 2026-10-22, before 09:24Z | 762 passed, 3 skipped, 4 failed |

The 4 failures are 1.5.0's: the two PostgreSQL-only tests that also fail on 1.4.2
(`test_bi_members::test_the_bi_page_saves_the_form`, `test_selfservice::test_sync_report_on_sqlite_returns_not_available`)
and the symlink test on both engines (`WinError 1314` on the Windows build PC).

---

## 3. Build and deploy

| Step | When (UTC) | Result |
| --- | --- | --- |
| Commit `Release 1.5.1` (first) | 09:24:58Z | `6829b2f`; wheel sha256 `4a984a03...529d42b`, kept as `.whl.6829b2f` |
| Backup `pg_dump -Fc drs` | 09:26Z | `runtime/pre_upgrade_1.5.1/drs.dump`, 71 577 764 bytes |
| Commit amended (not public): review fixes 3, 4, 10 | 09:44:33Z | `0edb7a5` |
| Wheel from `git -c core.autocrlf=false archive 0edb7a5` | 09:44Z | 3 015 963 bytes, sha256 `e4e1be1c05119491452a3532b8776c34083074aef1bb43780f603f72f92fa2b7` |
| `drscore-msodbc:1.5.1` / `drscore:1.5.1` (swap on the 1.5.0 images) | 09:45Z | `b685d9be0e30` / `2df422b40c80` |
| `.env` -> `.env.bak-1.5.0`; `DRSCORE_VERSION=1.5.1`; `docker compose up -d drscore` | 09:45Z | migrated 0014 -> 0015, `/healthz` ok, `db check` OK |

**Kept for rollback:** the 1.5.0 images and wheel, `.env.bak-1.5.0`, the dump above. Rollback to 1.5.0
needs migration 0015 down first (`html` links are switched off by it).

**Check on the worker:**
- Report `HQ_EXEC_DASH` (HTML, datasource `DRS_MIRROR`) imported; granted to `president_view` (no
  export, no refresh); first run 15.3 s, 1 691 rows.
- Public link 1 moved with `publiclink set 1 --report HQ_EXEC_DASH --view html --no-params
  --editable ''`: same address.
- Opened anonymously in headless Edge: page 200 in under 0.25 s, numbers on screen 0.4 s after the
  page; 1920x1080, 412x915, 915x412, 1280x800, 3840x2160; no console error, no horizontal scroll;
  titles follow the filters (Company 2, THAILAND), LOCAL currency on one company.
- Background swap: a temporary link with `--refresh 30` (revoked right after, links 2 and 3): the
  fresh copy replaced the shown one after about 28 s, currency VND, filters and scroll kept.

---

## 4. Publish

Not done: waits for the operator's word (`main` push, tags `v1.5.0` and `v1.5.1`).
