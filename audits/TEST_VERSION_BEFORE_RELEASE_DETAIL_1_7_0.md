# 1.7.0 - work list

**Opened 2026-11-09, at the operator's word** (ORD 440). The procedure is
[`TEST_VERSION_BEFORE_RELEASE.md`](./TEST_VERSION_BEFORE_RELEASE.md). Built on top of the uncommitted 1.6.4 working tree
of the private repository.

**Why this version exists:** the operator asked for drscore to do what the company's other portal already does: a
full log of every user action; a feedback section any signed-in user writes in and administrators handle; an AI
agent named Typhon; notices to a Telegram group and a Zalo group, each off until its group and bot key are set.
New tables, so a minor version. Reference page: [`docs/feedback-typhon-notices.md`](../docs/feedback-typhon-notices.md).

---

## 0. Where 1.7.0 stands

| Item | State |
| --- | --- |
| 1.1 | Activity log (`drs_activity_log`, admin page *Activity log*) - **done** |
| 1.2 | Encrypted integration secrets (`drs_secret`, `secret set / list / remove`) - **done** |
| 1.3 | Góp ý / Feedback (`/feedback`, files, thread, statuses) - **done** |
| 1.4 | AI agent Typhon (`/ai`, tools run as the user, limits, feedback intake) - **done**, off by default |
| 1.5 | Telegram / Zalo notices (`[notify]`, `notify status / test`) - **done**, off until configured |
| 1.6 | Release notes as petweb's: `drscore/CHANGELOG.md` + `CHANGELOG.vi.md`, version in the menu and on the sign-in page, `/changes`, "What's new" once per account and browser, `[admin]` / `[designer]` lines; a test refuses a version without its entry - **done** |
| 1.7 | Shell Kit 1.0 (`drscore/web/static/kit/`, README): the frame as petweb's, standalone and copyable to other apps; desktop / iPad / phone; and the admin's System pages - Nhật ký (filters, 3 logs, Excel), Góp ý as a table, AI agent overview / questions / settings, Telegram / Zalo notices with a test send - **done** |
| Migration | `0019`: 8 new tables, nothing changed in the existing ones |
| Dependency | `anthropic==2.6.0` (Apache-2.0) and what it pulls in: `httpx2`, `httpcore2`, `jiter`, `docstring-parser`, `sniffio`, `truststore` (THIRD_PARTY_LICENSES.md) |
| Tests | SQLite full suite: 505 passed, 6 skipped, 1 failed (the known Windows symlink test, §2); PostgreSQL - **not run** (§2) |
| Live AI check | one question through `agent.ask` with the real key on a throwaway SQLite copy of the demo - **passed** (§2) |
| Owner's settings (2026-11-09) | Typhon reads report data with a row cap (`max_report_rows`, new), who may ask per account (`allowed_users`, new) or role; for now only `report_designer`; 5 USD a month - **done** |
| Build / worker / check | wheel from tree `d96e9677`; full image build; on the worker since 2026-11-09T04:49Z, migration 0018 -> 0019 on PostgreSQL; Typhon checked (§3, §4) - **done** |
| Commit / publish | not yet: wait for the operator's word |

---

## 1. Items

### 1.1 Activity log

- `drscore.activity.ActivityMiddleware` (pure ASGI, outside the request context so a request refused there - a
  cross-site admin form - is logged too) writes one row after the response is sent, in its own transaction.
- Logged: every POST / PUT / PATCH / DELETE and admin action / import / export; every page GET (`[activity]
  page_opens`); the language switch, an export, a public link opened; every 4xx / 5xx. Not logged: static files,
  `/healthz`, a page's own reads (grid, options, frames) unless refused.
- Each row: user (session, sqladmin's admin, or the name typed at a failed sign-in), action (a table of ~60 named
  actions with Vietnamese / English labels), object key, method, path, query, status, `OK` / `DENIED` / `ERROR`,
  duration, client IP, browser, request id, and the fields sent - any field named like a password, secret, token,
  key or CSRF is `***`, long values cut at `max_field_chars`, uploads by size only, a public link's token cut to 6
  characters.

### 1.2 Encrypted integration secrets

- `drs_secret` holds `ai.api_key`, `telegram.bot_token`, `zalo.bot_token` as `enc:<Fernet>` with `DRS_SECRET_KEY`
  (the scheme of the datasource passwords). The environment variable named in app.toml wins when set.
- `python -m drscore secret set NAME [--stdin | --from-json FILE --field F]`: the value is never an argument and
  never printed; `SECRET_SET` / `SECRET_REMOVE` go to the audit log without it. A secret that cannot be decrypted
  (key changed) leaves its integration off; nothing fails.

### 1.3 Góp ý / Feedback

- `/feedback`: kind, urgency, the page it is about (filled from the menu link), message, up to 5 files of 8 MB
  (type from the first bytes), kept under `runtime/feedback/<id>/`, served to the sender and administrators only.
- Thread of answers; administrators set status / kind / urgency / fixed-in version; the sender writing on a closed
  feedback reopens it; the sender ticks a fixed one as checked. Menu badge of new feedback for administrators.

### 1.4 AI agent Typhon

- `[ai]`: off unless `enabled = true` and a key is found; then the menu shows *Typhon* (`/ai`), else every AI
  address answers 404 and nothing of it is shown.
- Claude through the official SDK, `claude-opus-5-5` at effort `medium` by default, server-side refusal fallback
  (`fallbacks: "default"`) on the Opus / Sonnet / Fable models, the system prompt cached.
- Tools run as the asking user through the portal's own functions: `list_reports`, `describe_report`,
  `my_feedback`, and `run_report` only with `read_report_data = true` (at most `max_rows_to_model` rows). It changes
  nothing.
- Limits on the server: `allowed_roles`, `questions_per_user_per_day`, `monthly_budget_usd` (one `ai_budget` notice
  when reached). Every question - answered, refused, failed - in `drs_ai_usage` with answer, tools, tokens, cost.
- Feedback intake: a new feedback is read first; its reply goes in the thread, its note to the administrators and in
  the notice.

### 1.5 Telegram / Zalo notices

- A channel sends only with both `chat_id` and its token. Events: `feedback_new`, `feedback_comment`,
  `feedback_reopened`, `design_submitted`, `ai_budget`. Sent from a background thread; each attempt in
  `drs_notice_log`, the token never written.

---

## 2. Tests

| Run | When | Result |
| --- | --- | --- |
| Full suite, SQLite, before any 1.7.0 test was added (no regression) | 2026-11-09 | 470 passed, 6 skipped, 1 deselected |
| `tests/test_integrations.py`, 22 new (activity log: failed sign-in, changes / pages / refusals, quiet reads, admin pages and a cross-site post, public token cut, the admin page; secrets: set from a JSON field, wrong key; feedback: send with a screenshot and handle, a disguised file refused; notices: only a ready channel, a failure without the token, a draft for approval; Typhon: off without a key, tools as the user, report data only when allowed, limits and roles, the monthly budget, an API failure, feedback intake; CLI status) | 2026-11-09 | 22 passed |
| Full suite, SQLite | 2026-11-09 | 491 passed, 6 skipped, 1 failed |
| `tests/test_changelog_system.py`, 14 new (the version gate on both changelogs, marks and escaping, what each reader sees, the menu's sections, the System pages for admins only, the log's filters and Excel, quiet JSON reads, the notices' test send) | 2026-11-09 | 14 passed |
| Full suite, SQLite, after 1.6 / 1.7 | 2026-11-09 | 505 passed, 6 skipped, 1 failed (the known symlink test) |
| Browser check (Playwright, local demo copy): every page at 1440x900, iPad 820x1180 and phone 390x844, the drawer, "What's new" per reader | 2026-11-09 | no console error; screenshots looked at |
| Full suite, PostgreSQL | 2026-11-09 | **not run**: the build PC is short of memory (the operator: no more Docker today). Migration 0019 on PostgreSQL is first exercised by the next PostgreSQL run - before the worker. |
| Live AI check: one question as `hr_aaa` on a throwaway SQLite with the demo, the real key read in memory only | 2026-11-09 | `ok`, model `claude-opus-5-5`, one `list_reports` round (only hr_aaa's 4 reports), tokens in 543 / out 278 / cache read 1 390 / cache write 1 390, 0.015 USD, 12.4 s |

Failure: `test_designs::test_a10_symlink_out_of_the_root_is_invalid` (Windows symlink privilege, as before).

---

## 3. Build and deploy

| Step | When (UTC) | Result |
| --- | --- | --- |
| Owner's Typhon settings in code: `[ai] allowed_users`, `[ai] max_report_rows` (a lower row cap only for Typhon's runs, `reports.service.run_row_cap`); 2 tests; changelog lines | 2026-11-09 ~04:35Z | `test_integrations` 24 passed |
| Full suite, SQLite | ~04:38-04:44Z | 507 passed, 6 skipped, 1 failed (Windows symlink) |
| Full suite, PostgreSQL | - | **not run**: no Docker on the build PC that day (operator). Migration 0019 was first run on the worker's PostgreSQL, after the dump below |
| Wheel from tree `d96e9677` | 04:45Z | 3 115 837 bytes, sha256 `123a974419b030e9b98604701439a1dfa185e6d540f3dfbefcdc1fdb04bf0fbd` |
| `.env`, `config/app.toml` -> `*.bak-1.6.4` | 04:46Z | done |
| `drscore:1.7.0` (`Dockerfile.wheel`, PyPI) / `drscore-msodbc:1.7.0` | 04:46-04:48Z | `156e3469b4ab` / `bf8ff27ba91b`; `anthropic 2.6.0` imports |
| `pg_dump -Fc`, before the upgrade | 04:48Z | `runtime/pre_upgrade_1.7.0/drs.dump`, 222 625 973 bytes |
| `config/app.toml` `[ai]`: enabled, read_report_data, max_rows_to_model 200, max_report_rows 10000, allowed_users ["report_designer"], 50 a day, 5 USD a month | 04:48Z | done |
| `DRSCORE_VERSION=1.7.0`; `docker compose up -d drscore` | 04:49Z | `DRS database migrated: revision 0018 -> 0019`, `/healthz` ok, `db check` OK |
| `secret set ai.api_key --stdin`, fed over SSH from the operator's secret store (no file, no argument) | 04:49Z | stored encrypted (106 characters); `ai status`: ON |

Disk of the worker after the build: 6.8 GB free (99 %). **Kept for rollback:** the 1.6.4 / 1.6.3 images, `*.bak-1.6.4`.

## 4. Check on the worker

- At `https://portal.example.com` as `report_designer`: `/`, `/ai`, `/feedback`, `/changes` answer 200.
- Typhon, one question (Company 2, 2026-11-06, one employee: in / out / late): it ran `HRMS_TIME_ALL_EMPLOYEE` as the user
  and answered 07:25 / 16:33 / 0 minutes - the same as the report. 6 138 input + 569 output tokens, 0.046 USD, 20 s.
- Activity log rows: `auth.login`, `page.open` x4, `ai.ask`.
- Telegram / Zalo: off (no group yet). Not checked: a feedback with a file answered by an administrator.

## Rollback

Back to 1.6.4 as §Rollback of the procedure. Migration 0019 only adds tables: 1.6.4 runs with them present.
