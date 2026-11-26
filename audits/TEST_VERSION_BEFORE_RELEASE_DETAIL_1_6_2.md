# 1.6.2 - work list

**Opened 2026-10-26, at the operator's word**, the same day as 1.6.1. The procedure is
[`TEST_VERSION_BEFORE_RELEASE.md`](./TEST_VERSION_BEFORE_RELEASE.md). Built from the private repository.

**Why this version exists:** after a dashboard's public link was switched off for named accounts
(1.6.1), the operator explained (translated): *switching the public link off meant switching off
viewing without signing in - the report should still open from a link like that, with the newest
data; keep the link, but check the rights, only those with rights get in. Every report like that:
its own link, quick to open, and "public" can be set on (viewable without signing in) or off.* The
portal's report page shows a design inside the page, and its "open in a new tab" shows one frozen
snapshot: neither is that link.

---

## 0. Where 1.6.2 stands

| Item | State |
| --- | --- |
| 1.1 | `drs_public_link.is_public` (migration 0018): a link may require a sign-in and the reader's own rights - **done** |
| 1.2 | CLI `publiclink add / set --public yes\|no`; `publiclink enable REF [--public no]`; `list` shows PUBLIC - **done** |
| Tests | full suite on SQLite and PostgreSQL: 808 passed, 4 failed - the 2 PostgreSQL-only failures of 1.4.2 onwards and 1 new test changed while the run went on, which passes on both engines when run again (§2) |
| Build | wheel from an LF export of the private working tree (a git tree, **not a commit**: §3) |
| Worker | `drscore-msodbc:1.6.2` running since 2026-10-26T05:51Z, migrated 0017 -> 0018 - **done** |
| Check | the switched-off link of a dashboard switched on again for signed-in readers, a second report given its own such link; both through Edge (§3) - **done** |
| Commit / publish | not yet: waits for the operator's word |

---

## 1. Items

### 1.1 Public or sign-in

- `drs_public_link.is_public`, true by default: every link made before stays public. Migration 0018
  adds the column; its downgrade switches the sign-in links off (never public after a downgrade)
  and drops it (SQLite: rebuilt keeping AUTOINCREMENT).
- A link that is not public, at all its addresses (`/p/<token>`, `/html`, `/bi-token`, `/list`):
  no DRS session -> the page answers with the sign-in page, which comes back to the link (the
  frame, the token and the list answer 401); a reader who must change their password -> the account
  page; a reader without a right on the report -> 403, without the report's name or data. Then the
  report runs as **the reader** (their grants, row filters, snapshot cache key) instead of `run_as`;
  the access log and a choice-list tick name the reader.
- `[public] enabled = false` closes the public links only; a sign-in link keeps working.
- `run_as` stays required and checked: the link closes when that user loses the report.

### 1.2 Commands

- `publiclink add ... --public no`, `publiclink set REF --public yes|no` (address kept, audited
  `PUBLICLINK_SET`).
- `publiclink enable REF [--public yes|no]`: a revoked link switched on again with its old address,
  checked again as `add` checks a link, audited `PUBLICLINK_ENABLE`; with `--public no` in the same
  step, so a link coming back for signed-in readers is never public in between.
- docs: `docs/public-links.md` ("Public or sign-in"), README.

---

## 2. Tests

| Run | When | Result |
| --- | --- | --- |
| `tests/test_publiclink.py`, 4 new (a sign-in link: the sign-in page, then the reader's own rows, the access log, 403 without rights; an HTML sign-in link: frame for the reader, 401 without a session, 403 without rights; public on / off keeps the address, the list column, a wrong value refused; a revoked link on again, and on again for signed-in readers in one step) | 2026-10-26 | 18 passed (SQLite) |
| Full suite, SQLite and PostgreSQL 16 | 2026-10-26, ~05:35Z | 808 passed, 3 skipped, 4 failed |
| `test_publiclink.py` + `test_auth.py`, SQLite and PostgreSQL 16, after the run | 2026-10-26, ~05:45Z | 66 passed |

Of the 4 failures, 2 are the PostgreSQL-only tests that fail since 1.4.2
(`test_bi_members::test_the_bi_page_saves_the_form`, `test_selfservice::test_sync_report_on_sqlite_returns_not_available`);
the other 2 are `test_a_revoked_link_can_be_switched_on_again` on both engines, edited (`enable --public`)
while the run was going on - it passes on both engines in the run after. Migration 0018 down and up
is exercised by `test_the_html_view_migration` (down to 0014 and up) on both engines.
`test_designs::test_a10_symlink_out_of_the_root_is_invalid` deselected (Windows symlink privilege).

---

## 3. Build and deploy

**Not as §3 rule 2 says:** the wheel was built from the working tree of the private repository, not
from a commit. Working tree staged into a temporary index, written as git tree `ff84dbdc`, exported
with `git -c core.autocrlf=false archive <tree>`.

| Step | When (UTC) | Result |
| --- | --- | --- |
| Wheel from tree `ff84dbdc` | 2026-10-26 ~05:49Z | 3 029 974 bytes, sha256 `29e76a2544dd4f08b1455e038fdc2f622cd727b27ade333dca785a32a87e0afe` |
| `pg_dump -Fc` of the DRS database, **before** the upgrade | 05:49:51-05:50:13Z | `runtime/pre_upgrade_1.6.2/drs.dump`, 149 691 892 bytes |
| `drscore:1.6.2` / `drscore-msodbc:1.6.2` | 05:50-05:51Z | `d9ffaf1aa270` / `9074de1dcf3d` |
| `.env` -> `.env.bak-1.6.1`; `DRSCORE_VERSION=1.6.2`; `docker compose up -d drscore` | 05:51:01-05:51:05Z | migrated 0017 -> 0018, `/healthz` ok, `db check` ok |

**Kept for rollback:** the 1.6.1 images and wheel, `.env.bak-1.6.1`, the dump above. Rollback to
1.6.1: migration 0018 down first (it switches the sign-in links off), then `DRSCORE_VERSION=1.6.1`.

**Check on the worker**, headless Edge: the dashboard link switched off in 1.6.1 switched on again
with `publiclink enable 1 --public no`; a second report given its own link `--public no`. For each:
opened without a session -> the sign-in page; signed in as its view-only account -> back on the
link, the design full page with the newest snapshot, no console error; the browser closed and opened
again on the same profile -> the link opens without the sign-in page (1.6.1 session days); the other
account -> 403 "no right to view this report", no frame, no data.

---

## 4. Publish

Not published. Before a public release: commit in the private repository, push to the public one,
check that nothing internal is in it, tag `v1.6.2` there.
