# 1.11.0 - work list

**Opened 2026-11-26, at the operator's word.** The procedure is
[`TEST_VERSION_BEFORE_RELEASE.md`](./TEST_VERSION_BEFORE_RELEASE.md). Built from the private repository, commit `961787f`.

**Why this version exists:** the public repository held, uncommitted since 2026-10-28, mirror copies on PostgreSQL as
UNLOGGED tables. The operator: *"đưa sang private đi"*; then *"Ok"* to deploying it and publishing it.

---

## 0. Where 1.11.0 stands

| Item | State |
| --- | --- |
| 1.1 | `[mirror] unlogged` (default `true`): on PostgreSQL a mirror's table is created UNLOGGED, an existing one is switched once (`ALTER TABLE ... SET UNLOGGED / LOGGED`) - **done** |
| 1.2 | An incremental mirror whose copy is empty (an unlogged table is emptied after a crash of its server) makes a full copy instead of trusting its watermark - **done** |
| Tests | mirror tests on SQLite and on PostgreSQL; full suite on SQLite (§2) |
| Worker | 1.11.0 since 2026-11-26T14:01Z (§3) - **done** |
| Commit / publish | private `961787f` (pushed); public: one squashed commit from a sanitized export, tag `v1.11.0` (§4) |

No migration, no new Python dependency, no Superset change.

## 1. Items

- `settings.py`: `MirrorSection.unlogged: bool = True`.
- `mirror/runner.py`: `target_table(..., unlogged=)` (prefix `UNLOGGED`), `wants_unlogged()` (PostgreSQL only),
  `_set_persistence()` in `ensure_target`, `_holds_rows()` and the full copy of an empty incremental copy.
- `docs/mirror.md` "Unlogged copies"; `unlogged = true` in `config/app.example.toml` and the packaged
  `drscore/examples/app.example.toml` (a test compares the two); changelog VI / EN.
- `tests/test_mirror.py`: two tests (the table is unlogged, and switched back to logged; an empty incremental copy
  is filled in full).

## 2. Tests

| Run | When | Result |
| --- | --- | --- |
| `tests/test_mirror.py`, SQLite, build PC | 2026-11-26 | 16 passed |
| `tests/test_mirror.py`, PostgreSQL (test container, not the worker's database) | 2026-11-26 | 15 passed, 15 deselected (the PostgreSQL cases) |
| Full suite, SQLite, build PC | 2026-11-26 | 518 passed, 3 failed = `test_default_admin.py` x2 (fail on the unchanged tree too) and the known Windows symlink test |

## 3. Build and deploy

| Step | When (UTC) | Result |
| --- | --- | --- |
| Before: persistence of the mirror schema's tables (read through the app's engine) | 13:55Z | 56 tables, 4.8 GB; 55 already unlogged (switched by hand earlier), 1 logged that is no mirror's target |
| Wheel from `961787f` (LF export) | 13:58Z | sha256 `08c313c7b28c29678b9e4e685168cff84402e8c56ec52be97b45e2dcd760d464` |
| `drscore-msodbc:1.11.0` = swap on 1.10.0 | 14:00Z | `f0b29a9f1cdb` |
| `pg_dump -Fc` before | 14:01Z | `runtime/pre_upgrade_1.11.0/drs.dump`, 246 410 758 bytes |
| `.env` -> `.env.bak-1.10.0`, `docker compose up -d drscore` | 14:01Z | running 1.11.0, healthz ok, no error in the log |
| One small full mirror run by hand | ~14:05Z | ok, its table unlogged; `mirror list`: 99 ok, 1 running |

The first run after the deploy changes nothing on this worker: every mirror's table was unlogged already. The one
logged table is no mirror's target, and the code leaves it alone.

Rollback: `DRSCORE_VERSION=1.10.0` in `.env` + `docker compose up -d drscore`. The tables stay unlogged; with
`[mirror] unlogged = false` the next run of each mirror switches its table back to logged.

## 4. Publish

Public repository: one squashed commit "Release 1.11.0" from a sanitized export of the private HEAD (internal
addresses, users and company names replaced in the audit sheets), tag `v1.11.0`, GitHub Actions -> PyPI. The ledger
row says `deployed` until the tag's upload is seen on PyPI.
