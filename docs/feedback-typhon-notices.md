# Activity log, feedback (Góp ý), the AI agent Typhon, Telegram / Zalo notices

DRS 1.7.0 adds four things that work together:

| What | Where | Off / on |
| --- | --- | --- |
| **Activity log**: every request a user makes, with its outcome | Admin -> Logs -> *Activity log* | on (`[activity]`) |
| **Góp ý / Feedback**: any signed-in user writes to the administrators | `/feedback` (menu *Góp ý*) | on (`[feedback]`) |
| **Typhon**, the AI agent: answers questions about reports and the portal, reads new feedback first | `/ai` (menu *Typhon*) | off until enabled **and** given a key (`[ai]`) |
| **Notices** to a Telegram group and a Zalo group | the groups | each off until its group **and** bot token are set (`[notify]`) |

Migration `0019` makes their tables (`drs_activity_log`, `drs_secret`, `drs_feedback`, `drs_feedback_comment`,
`drs_feedback_file`, `drs_ai_conversation`, `drs_ai_usage`, `drs_notice_log`). `serve` applies it.

## Activity log

`drs_access_log` (report opened / run / viewed / exported) and `drs_audit_log` (a row before and after each change)
stay as they were. The activity log is the trail of the requests that led to them, and of everything else:

- every change: sign-in (failed ones with the name typed), sign-out, password change, the designer's new / save /
  test / fill columns / submit / withdraw / delete / publish / approve / reject, the BI page's dataset / link /
  parameters / unlink / sync, a public link's choice list saved, feedback sent / answered / changed / checked, a
  question to Typhon, and every create / edit / delete / action / import / export on the admin pages;
- every page opened (`page_opens = true`), the language switched, a report exported, a public link opened;
- every request refused (401 / 403: no right, a missing CSRF token, a cross-site admin form) or failed (4xx / 5xx).

Each row has the user (or `-`), the action and its object (report code, draft number, admin table), the
address and its query, the HTTP status and `OK` / `DENIED` / `ERROR`, the time it took, the client address and
browser, the request id of `runtime/logs/drs.log`, and **what was sent**: the form or JSON fields, where any
field whose name says password, secret, token, key or CSRF is `***`, and a value longer than `max_field_chars`
is cut. A file upload keeps only its size. A public link's token keeps only its first 6 characters.

The row is written after the response is sent, in its own transaction: logging never slows or breaks a page.
Static files, `/healthz` and the reads a page makes by itself (a grid, a parameter's options, a frame) are not
logged unless refused.

```toml
[activity]
enabled = true
page_opens = true
max_field_chars = 4000
```

## Góp ý / Feedback

Any signed-in user opens *Góp ý* from the menu (the page they were on is filled in), picks a kind (bug, idea,
question) and an urgency, writes, and may attach up to `max_files` files of `max_file_mb` each: images (PNG,
JPEG, WebP, GIF), PDF, XLSX, DOCX, CSV, TXT - told by their first bytes, never by their name. Files are kept
under `files_dir` (`runtime/feedback/<number>/`), served only to the sender and the administrators.

- The sender sees their own feedback, its thread and status; writing again on a closed one reopens it; a fixed
  one can be ticked *checked*.
- An administrator sees all of them (a red count of new ones on the menu), answers in the thread (the status
  goes from *new* to *seen*), and sets the status (new, seen, in progress, done, won't fix), the kind, the
  urgency and the version it was fixed in. Admin -> Logs -> *Feedback* lists them too.

```toml
[feedback]
enabled = true
files_dir = "runtime/feedback"
max_files = 5
max_file_mb = 8
```

## Typhon, the AI agent

Typhon (the name is `[ai] name`) runs on Claude, through the official Anthropic SDK, on the server only: no page
ever sees the key. It is **off** - nothing of it shown, its addresses answer "not found" - until both:

1. `[ai] enabled = true`, and
2. an API key is found: the environment variable `api_key_env` (`DRS_AI_API_KEY`), else the encrypted secret
   `ai.api_key` (below).

What it does:

- **Answers a user** on `/ai` (the menu passes the page they came from). It knows how the portal works and looks
  things up with tools that run **as that user**, through the same functions as the pages, so every right and
  row filter applies: the reports the user may open (`list_reports`), a report's parameters and views
  (`describe_report`), the user's own feedback (`my_feedback`), and - only with `read_report_data = true` - a
  report run with the user's parameters (`run_report`, at most `max_rows_to_model` rows to the model; the run is
  in the access log like any other). A run that would give more than `max_report_rows` rows stops at once
  (reports can reach hundreds of thousands of rows): Typhon is told so and asks for narrower parameters, or
  sends the user to the report's own export. A result already in the cache is read as it is. It changes nothing.
- **Reads a new feedback first** (`feedback_intake = true`): its reply goes in the thread (as *Typhon*), its
  note for the administrators on the feedback's page (administrators only) and in the Telegram / Zalo notice.
  Without the agent the notice goes out at once.

Limits, checked on the server: `allowed_users` (usernames) and `allowed_roles` (role codes) - a user in either
may ask, administrators always, both empty = every signed-in user; `questions_per_user_per_day`, `monthly_budget_usd` (in the time zone of `[app] timezone`; when reached
it answers "limit reached" until next month, and the groups get one `ai_budget` notice). Every question -
answered, refused or failed - is a row of Admin -> Logs -> *AI agent questions*: who, from which page, the
answer, every tool run, the tokens and the cost (from `prices`, USD per million tokens). Conversations are kept
per user.

The model is `claude-opus-5-5` by default, at effort `medium`. On the Opus / Sonnet / Fable models it asks for
Anthropic's server-side refusal fallback (`fallbacks: "default"`): a request the model's safety classifiers
decline is re-run on the model Anthropic recommends, inside the same call. `base_url` points at a relay of the
API when the server cannot reach it directly (the fallback is then not asked for).

```toml
[ai]
enabled = true
name = "Typhon"
model = "claude-opus-5-5"
effort = "medium"
read_report_data = true
max_rows_to_model = 200
max_report_rows = 10000
allowed_users = ["report_designer"]
allowed_roles = []
questions_per_user_per_day = 50
monthly_budget_usd = 5.0
feedback_intake = true
```

`python -m drscore ai status` says whether it is on, and why not.

## Notices to Telegram and Zalo

Each channel sends only when **both** its group and its bot token are set; until then nothing is sent and
nothing fails. Events (`[notify] events`): `feedback_new` (with Typhon's note when it read it),
`feedback_comment` (the sender wrote again), `feedback_reopened`, `design_submitted` (a draft waits for
approval), `ai_budget`. Each notice carries the link to the page, from `[app] base_url`.

```toml
[notify]
prefix = ""                       # e.g. "[TEST] " on a test installation

[notify.telegram]
chat_id = "-100..."               # the group: add the bot to it; its id from getUpdates or a bot like @RawDataBot
bot_token_env = "DRS_TELEGRAM_BOT_TOKEN"

[notify.zalo]
chat_id = ""                      # the Zalo bot's id of the group
bot_token_env = "DRS_ZALO_BOT_TOKEN"
```

Notices are sent from a background thread after the request. Every attempt is a row of Admin -> Logs -> *Notices
sent* (text, sent or the error); the token is never written there nor in `drs.log`.

```bash
python -m drscore notify status          # which channel is ready, and what is missing
python -m drscore notify test            # one test message to every ready channel
```

## Keeping the key and the tokens secret

The AI key and the bot tokens are never written in a file in clear, in git or in the database. Either:

- **On a server (recommended): stored encrypted in the DRS database.** The value is encrypted with
  `DRS_SECRET_KEY` (Fernet, the same key and scheme as the datasource passwords); the database holds only the
  ciphertext, the environment only the encryption key, no file holds the secret.

  ```bash
  python -m drscore secret set ai.api_key                     # hidden prompt, typed twice
  python -m drscore secret set ai.api_key --stdin             # one line from standard input
  python -m drscore secret set ai.api_key --from-json FILE --field NAME   # a field of a JSON file
  python -m drscore secret set telegram.bot_token --stdin
  python -m drscore secret list                               # stored or not, never the value
  python -m drscore secret remove zalo.bot_token
  ```

  The value is never printed and never taken as an argument (it would stay in the shell history). Each change is
  in the audit log (`SECRET_SET` / `SECRET_REMOVE`, without the value).

- **Or in the environment variable** named in app.toml (`DRS_AI_API_KEY`, ...), which wins when it is set -
  convenient on a build PC, but then the value sits in the service's environment (`.env`, `docker inspect`).

Someone who can read **both** the database and `DRS_SECRET_KEY` can decrypt; so keep `DRS_SECRET_KEY` only in
the service's environment file, readable by root alone (`chmod 600`), never in the image or in git, and the
database reachable only by DRS. Changing `DRS_SECRET_KEY` makes the stored secrets unreadable (the integration
turns itself off, nothing fails): store them again after a key change, as for the datasource passwords.
