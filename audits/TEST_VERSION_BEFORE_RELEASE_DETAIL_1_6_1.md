# 1.6.1 - work list

**Opened 2026-10-26, at the operator's word.** The procedure is
[`TEST_VERSION_BEFORE_RELEASE.md`](./TEST_VERSION_BEFORE_RELEASE.md). First version built from the
private repository (owner, 2026-10-26: fixes are made and deployed from there; the public repository
gets a version only when it is published).

**Why this version exists:** a dashboard that was opened from a public link is now opened by a
named, view-only account. The operator, 2026-10-26 (translated): *a user that can only view this
report, no password change at the first sign-in, and the session should live 30 days - through
closing the browser and turning the computer off and on.* The session limits of DRS were one
setting for everybody (`[session] idle_minutes = 60`, `absolute_hours = 12`); making them 30 days
for everybody would keep administrators and designers signed in for a month too. The operator chose
a per-user setting.

---

## 0. Where 1.6.1 stands

| Item | State |
| --- | --- |
| 1.1 | `drs_user.session_days`: one sign-in of that user lasts that many days (migration 0017) - **done** |
| 1.2 | CLI `user set-session USER --days N / --default`; field "Session days" on the admin user form - **done** |
| Tests | full suite on SQLite and PostgreSQL: 802 passed, 2 failed - the 2 PostgreSQL-only failures of 1.4.2 onwards (§2) |
| Build | wheel from an LF export of the private working tree (a git tree, **not a commit**: §3) |
| Worker | `drscore-msodbc:1.6.1` running since 2026-10-26T04:46Z, migrated 0016 -> 0017 - **done** |
| Check | two view-only accounts signed in through Edge: 30-day cookie, still signed in after the browser was closed and opened again (§3) - **done** |
| Commit / publish | not yet: waits for the operator's word |

---

## 1. Items

### 1.1 Session days per user

- `drs_user.session_days` (integer, empty by default; migration 0017, nullable column, downgrade
  drops it). `auth.session_lifetime(user, settings)` gives the idle and the absolute limit: both
  `session_days` days when set, else `[session] idle_minutes` / `absolute_hours`.
- `create_session` sets `expires_at` from it; `load_session` checks the idle limit from it; the
  sign-in cookie's `Max-Age` is the absolute limit, so the browser keeps the cookie when it is
  closed (it already had a `Max-Age`; it was 12 hours).
- `purge_sessions` ends a session idle beyond `[session] idle_minutes` only for users without
  `session_days`: for them the idle limit is never earlier than `expires_at`.
- A change applies from the user's next sign-in; open sessions keep their end time. Audited
  `USER_SET_SESSION` (before / after).

### 1.2 Setting it

- CLI `python -m drscore user set-session USER --days N` (1-366) or `--default`; `user list` shows a
  `SESSION` column.
- Admin pages, Users: "Session days" (1-366 or empty, checked on save), Vietnamese label and help.
- docs: README (CLI table), `docs/user-guide-admin.md`.

---

## 2. Tests

| Run | When | Result |
| --- | --- | --- |
| `tests/test_auth.py` (3 new: 30 days kept past the idle limit and by the purge, back to the default; bounds 0 / -1 / 367 refused; the sign-in cookie carries `Max-Age` 30 days) | 2026-10-26 | 15 passed |
| Full suite, SQLite | 2026-10-26 | 459 passed, 6 skipped, 1 failed (Windows symlink privilege, as on 1.6.0) |
| Full suite, SQLite and PostgreSQL 16 | 2026-10-26, before 04:40Z | 802 passed, 3 skipped, 2 failed |

The 2 failures are the PostgreSQL-only tests that fail since 1.4.2
(`test_bi_members::test_the_bi_page_saves_the_form`, `test_selfservice::test_sync_report_on_sqlite_returns_not_available`).
`test_designs::test_a10_symlink_out_of_the_root_is_invalid` deselected (Windows symlink privilege).

---

## 3. Build and deploy

**Not as §3 rule 2 says:** the wheel was built from the working tree of the private repository, not
from a `Release 1.6.1` commit - a commit waits for the operator's word. The working tree was staged
into a temporary index (the real index untouched), written as git tree `d75b5efc` and exported with
`git -c core.autocrlf=false archive <tree>` (LF, no tests).

| Step | When (UTC) | Result |
| --- | --- | --- |
| Wheel from tree `d75b5efc` | 2026-10-26 ~04:33Z | 3 027 691 bytes, sha256 `31b7e79f16516862dac742370640b67967fc173fb84d7398f96623db60130aa7` |
| `pg_dump -Fc` of the DRS database, **before** the upgrade | 04:44:59-04:45:22Z | `runtime/pre_upgrade_1.6.1/drs.dump`, 149 182 274 bytes |
| `drscore:1.6.1` / `drscore-msodbc:1.6.1` | 04:45-04:46Z | `0854c0ac8c70` / `aa140aa8bfd3` |
| `.env` -> `.env.bak-1.6.0`; `DRSCORE_VERSION=1.6.1`; `docker compose up -d drscore` | 04:46:15-04:46:20Z | migrated 0016 -> 0017, `/healthz` ok, `db check` ok |

**Kept for rollback:** the 1.6.0 images and wheel, `.env.bak-1.6.0`, the dump above. Rollback to
1.6.0 = `DRSCORE_VERSION=1.6.0` and `docker compose up -d drscore`; the extra nullable column does
not disturb 1.6.0 (or migration 0017 down first).

**Check on the worker**, headless Edge with a kept browser profile, for each of two view-only
accounts (one report each, view without export or refresh, `session_days` 30, created with the CLI -
no password change asked at the first sign-in):
- signed in straight to the home page; cookie `drs_session` HttpOnly, expires in 30.0 days;
- its report opened and drawn, no console error; the other account's report and `/admin/` answer
  403; the export of the run on screen answers 403 `EXPORT_NOT_ALLOWED` (xlsx, csv), no export button;
- the browser closed and opened again on the same profile: the report opens, still signed in.

---

## 4. Publish

Not published. Before a public release: commit in the private repository, push to the public one,
check that nothing internal is in it, tag `v1.6.1` there.
