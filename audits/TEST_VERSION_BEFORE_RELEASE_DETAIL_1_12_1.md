# 1.12.1 - work list

**Opened 2026-11-27, a fix found in use.** The procedure is
[`TEST_VERSION_BEFORE_RELEASE.md`](./TEST_VERSION_BEFORE_RELEASE.md). Built from the private working tree (tree
`3c7cb43f0b18`, uncommitted: commits wait for the operator's word).

**Why this version exists:** the operator opened a sign-in link (not public) with the dashboard view, signed in as an
administrator, and got "The dashboard could not be loaded". The worker's log showed every
`GET /p/<token>/bi-token` answered `401`: the public page fetched the guest token with `credentials: "omit"`, so a
sign-in link's token request carried no session and the server answered `AUTH_REQUIRED`. Public links (ORD 484) run as
the link's user and never needed the cookie, which is why it was not seen before. The server side was right; the page
was not.

---

## 0. Where 1.12.1 stands

| Item | State |
| --- | --- |
| 1.1 | `public.js` fetches the token with `credentials: "same-origin"` - **done** |
| Tests | new `test_a_sign_in_dashboard_link_gives_its_signed_in_reader_a_token`; full suite (§2) |
| Worker | 1.12.1 since 2026-11-27T05:50Z (§3) - **done**, the operator's link checked |
| Commit / publish | not yet - waits for the operator's word |

No migration, no setting, no new dependency, no Superset change.

## 1. Items

- `drscore/web/static/js/public.js`: the token fetch sends the session cookie (same origin only). A public link's token
  still runs as the link's user (`_reader` ignores the session for a public link).
- Changelog VI / EN 1.12.1; `pyproject.toml` 1.12.1.

## 2. Tests

| Run | When | Result |
| --- | --- | --- |
| `tests/test_publiclink.py` | 2026-11-27 | 31 passed (new test: anonymous `AUTH_REQUIRED`, signed-in reader gets a token, the page script sends the session) |
| Changelog, BI, BI members, public link, report schedule tests | 2026-11-27 | 93 passed, 1 skipped (PostgreSQL-only view test) |
| Full suite, SQLite, build PC | 2026-11-27 | 541 passed, 6 skipped, 3 failed = the same three as 1.12.0 (`test_default_admin.py` x2, the Windows symlink test) |

## 3. Build and deploy

| Step | When (UTC) | Result |
| --- | --- | --- |
| Wheel from the working tree (LF export of tree `3c7cb43f0b18`) | 05:45Z | sha256 `36d0305cec1128815e2a63b96ce88eeacb7384ed41bc73e792410694b2dbed6f` |
| `drscore-msodbc:1.12.1` = swap on 1.12.0 | 05:49Z | `45c559bd7558` |
| `pg_dump -Fc` before | 05:50Z | `runtime/pre_upgrade_1.12.1/drs.dump`, 247 020 792 bytes |
| `.env` -> `.env.bak-1.12.0`, `docker compose up -d drscore` | 05:50Z | running 1.12.1, healthz ok; the served `/static/js/public.js` has `same-origin` |
| Link 17 (sign-in, BI view) opened at the IP address, signed in | 05:55Z | `bi-token` 200, dashboard shown (four tabs, data as of 12:13 +07) |

Note: `pip wheel` run with the build folder as the working directory and a relative source path failed
(`'NoneType' object has no attribute 'has_pure_modules'`, built as "src"); the same command with absolute paths from
the repository built the wheel. Build as for 1.12.0.

Rollback: `DRSCORE_VERSION=1.12.0` in `.env` + `docker compose up -d drscore`.

## 4. Publish

Not yet: waits for the operator's word (commit in private, then one squashed public commit, tag `v1.12.1`).
