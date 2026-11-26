# 1.6.3 - work list

**Opened 2026-10-26, at the operator's word**, the same day as 1.6.1 and 1.6.2. The procedure is
[`TEST_VERSION_BEFORE_RELEASE.md`](./TEST_VERSION_BEFORE_RELEASE.md). Built from the private repository.

**Why this version exists:** a link to a dashboard whose report runs on a date range lets the reader
change the dates (`--editable from_date,to_date`). The bar above the page showed two **empty text
boxes**: the reader could not see which range the page showed, nor pick a date. Found while checking
such a link in a browser.

---

## 0. Where 1.6.3 stands

| Item | State |
| --- | --- |
| 1.1 | an editable parameter of a link shows the value the report runs with (its default resolved), in an input of its type - **done** |
| Tests | full suite on SQLite and PostgreSQL: 812 passed, 2 failed - the 2 PostgreSQL-only failures of 1.4.2 onwards (§2) |
| Build | wheel from an LF export of the private working tree (git tree `2d42d010`, **not a commit**) |
| Worker | `drscore-msodbc:1.6.3` running since 2026-10-26T06:50Z, no migration - **done** |
| Check | the date bar of a sign-in link: date pickers with the range in use; a new date applied re-runs the page (§3) - **done** |
| Commit / publish | not yet: waits for the operator's word |

---

## 1. Items

### 1.1 Editable parameters show their value

- The bar's input of an editable parameter holds, in this order: the reader's value (query string),
  the link's value, the parameter's default **resolved** (`@month_start-2m` -> `2026-08-01`). A
  default that depends on the reader (`@user.<attribute>`) or cannot be read leaves it empty, as before.
- The input has the parameter's type: `date` -> a date picker, `int` / `decimal` -> a number input,
  else text (a list of options stays a select).
- docs: `docs/public-links.md` (`--editable`).

---

## 2. Tests

| Run | When | Result |
| --- | --- | --- |
| `tests/test_publiclink.py`, 1 new (a date picker with the resolved defaults; the reader's value kept), SQLite and PostgreSQL 16 | 2026-10-26 | 38 passed |
| Full suite, SQLite and PostgreSQL 16 | 2026-10-26, ~06:35-06:45Z | 812 passed, 3 skipped, 2 failed |

The 2 failures are the PostgreSQL-only tests that fail since 1.4.2
(`test_bi_members::test_the_bi_page_saves_the_form`, `test_selfservice::test_sync_report_on_sqlite_returns_not_available`).
`test_designs::test_a10_symlink_out_of_the_root_is_invalid` deselected (Windows symlink privilege).

---

## 3. Build and deploy

| Step | When (UTC) | Result |
| --- | --- | --- |
| Wheel from tree `2d42d010` | 2026-10-26 ~06:49Z | 3 030 336 bytes, sha256 `dc5790542c1ab32bf135c77f5090b5663f32262302bca36855b5cebf062e6804` |
| `pg_dump -Fc` of the DRS database, before the upgrade | 06:49:25-06:50:02Z | `runtime/pre_upgrade_1.6.3/drs.dump`, 207 969 764 bytes |
| `drscore:1.6.3` / `drscore-msodbc:1.6.3` | 06:50Z | `0f9ececaa745` / `2bc06d730430` |
| `.env` -> `.env.bak-1.6.2`; `DRSCORE_VERSION=1.6.3`; `docker compose up -d drscore` | 06:50:46-06:50:51Z | no migration, `/healthz` ok, `db check` ok |

**Kept for rollback:** the 1.6.2 images and wheel, `.env.bak-1.6.2`. Rollback = `DRSCORE_VERSION=1.6.2`,
`docker compose up -d drscore` (no migration between them).

**Check on the worker**, headless Edge, a sign-in link whose report has `from_date` (`@month_start-2m`)
and `to_date` (`@today`) editable: the bar shows two date pickers holding the first day of the month two
months back and today; the design shows the same range; a new first date applied -> the address carries
it, the report runs for it and the design shows the new range; no console error.

---

## 4. Publish

Not published. Before a public release: commit in the private repository, push to the public one,
check that nothing internal is in it, tag `v1.6.3` there.
