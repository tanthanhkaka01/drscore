# 1.12.0 - work list

**Opened 2026-11-27, at the operator's word.** The procedure is
[`TEST_VERSION_BEFORE_RELEASE.md`](./TEST_VERSION_BEFORE_RELEASE.md). Built from the private repository, commit `cfd4ebb`.

**Why this version exists:** a set of scan reports must read the production HR server once a night between 01:00
and 05:00, never during the day. DRS read a source only when someone opened a report whose snapshot had expired,
at whatever hour that was. The operator asked whether DRS could take a retention and a time window "like dbabrain",
and to upgrade it if not; then *"làm như plan nhé"*.

---

## 0. Where 1.12.0 stands

| Item | State |
| --- | --- |
| 1.1 | Report schedules: a report reads its source by itself, with its default parameters, when its window is open and its interval has passed - **done** |
| 1.2 | The time window has dbabrain's fields and meaning (`from_`/`to_` year, month, day, hour, minute, inclusive and wrapping; ISO `weekdays`; `repeat_interval` from the last start, `0` once, `-1` manual; `retry_interval`; `timeout`) - **done** |
| 1.3 | `live_runs 'window'`: outside the window a reader gets the newest snapshot kept and no Refresh; nothing a reader does reads the source - **done** |
| Tests | SQLite full suite; schedule, cache, database and CLI tests on PostgreSQL (§2) |
| Worker | 1.12.0 since 2026-11-27T03:30Z, migration 0021, `[schedule] enabled = true` (§3) - **done** |
| Commit / publish | private `cfd4ebb` (pushed); public: §4 |

Migration 0021 (`drs_report_schedule`). No new Python dependency, no Superset change.

**Changed from the plan:** the plan put the schedule in the designer's form. It is an administrator's setting
(CLI `report schedule`, read-only list on the admin pages), as a report's timeout already is: how hard a report may
press on a production server is the operator's call, and the draft / approval content stays as it was.

## 1. Items

- `reports/window.py`: the time window (parse, refusing unknown fields, negative values, `0` as a weekday and
  repeats; open / closed reason; due from the last start with a 5 % / 30 s grace, as dbabrain).
- `reports/schedule.py`: set / on / off with the audit log; claim in the database (`lock_minutes`); `run_one`
  (always reads the source, keeps the snapshot for the retention, loads the BI dataset, access log as `scheduler`);
  `due`, `run_due`; `ReportScheduler` thread in `serve` (`[schedule] enabled`, `poll_seconds`).
- `reports/service.py`: `Prepared.live_window`; `get_result(..., scheduled=)` serves the newest snapshot kept outside
  the window (`Outcome.held`), or `OUTSIDE_RUN_WINDOW` when there is none. `cache warm` counts as an administrator's run.
- Web: the report page shows "This report reads its data only at set times (...)", and no Refresh outside the window.
- `db/models.py` `ReportSchedule`, migration 0021; `settings.py` `[schedule]`; CLI `report schedule set|list|run|on|off`;
  admin `report-schedule` (read-only) under Monitoring > Logs; i18n VI / EN; `docs/report-schedule.md`; changelog.

## 2. Tests

| Run | When | Result |
| --- | --- | --- |
| `tests/test_report_schedule.py`, SQLite | 2026-11-27 | 21 passed (window rules, due, run, failures, readers kept to the window, page, API, admin list, CLI) |
| Full suite, SQLite, build PC | 2026-11-27 | 538 passed, 6 skipped, 3 failed = `test_default_admin.py` x2 (fail on the unchanged tree too) and the known Windows symlink test |
| Schedule, cache, database and report CLI tests, PostgreSQL (test container, not the worker's database) | 2026-11-27 | 40 passed |

## 3. Build and deploy

| Step | When (UTC) | Result |
| --- | --- | --- |
| Wheel from `cfd4ebb` (LF export) | 03:27Z | sha256 `f6372548a73ecdfd0d3aa9feb588fcba527f863249a962b313f4f8ad29a3568e` |
| `config/app.toml` -> `app.toml.bak-1.11.0`; `[schedule] enabled = true, poll_seconds = 60, lock_minutes = 60` added | 03:28Z | done |
| `drscore-msodbc:1.12.0` = swap on 1.11.0 | 03:29Z | `c9911055368` |
| `pg_dump -Fc` before | 03:30Z | `runtime/pre_upgrade_1.12.0/drs.dump`, 246 324 413 bytes |
| `.env` -> `.env.bak-1.11.0`, `docker compose up -d drscore` | 03:30Z | running 1.12.0, "DRS database migrated: revision 0020 -> 0021", healthz ok, "report scheduler started (every 60 s)" |
| Nine scan reports given a schedule (01:00-04:59, every 72000 s, timeout 600 / 900, live runs window) | 03:47Z | listed; window closed at 10:xx local, not due |
| A republished report kept to its window: the publish's dataset preparation | 03:57Z | refused with OUTSIDE_RUN_WINDOW (as designed); `report schedule run` then loaded it (1.1 s) |

Rollback: `DRSCORE_VERSION=1.11.0` in `.env` + `docker compose up -d drscore` (1.11.0 ignores the new table and the
`[schedule]` section); `config/app.toml.bak-1.11.0`.

**Lesson (not a defect of this version):** publishing a new report prepares its BI dataset by running it once with
its default parameters, before any schedule can exist for it. A heavy report published during the day therefore
reads its source at once. Give such a report a light default for the test and the publish, or publish it inside
its window; once it has a schedule with live runs `window`, later publishes no longer read the source outside it.

## 4. Publish

Public repository: one squashed commit "Release 1.12.0" from a sanitized export of the private HEAD, tag `v1.12.0`,
GitHub Actions -> PyPI. The ledger row says `deployed` until the upload is seen on PyPI.
