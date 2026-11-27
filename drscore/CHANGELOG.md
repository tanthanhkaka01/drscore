# DRS versions

Every code change raises the version (`pyproject.toml`): a fix raises the last number (1.6.3 → 1.6.4), a new feature the middle one (1.6.4 → 1.7.0). The version shows at the bottom of the left menu and on the sign-in page; clicking it lists the changes, and a new version shows "What's new" once on each browser by itself. Each line says who reads it: `[admin]` administrators only, `[designer]` report designers and administrators, no mark everyone; a version with no line for an account shows it "System improvements and small fixes." The Vietnamese file is `CHANGELOG.vi.md`, with the same versions; the test suite fails when the version of `pyproject.toml` is not at the top of both files.

## 1.12.0 — 2026-11-27
- [admin] **Report schedules**: a report can read its source by itself at set times, with its default parameters, and keep the result for its retention (`python -m drscore report schedule set|list|run|on|off`; `[schedule] enabled = true`). The time window has dbabrain's fields and meaning - `from_hour` / `to_hour` (inclusive, wrapping midnight), minutes, days, months, years, ISO `weekdays`, `repeat_interval` (seconds between starts; `0` once, `-1` manual), `retry_interval`, `timeout` - so "once a night between 01:00 and 05:00" is `--from-hour 1 --to-hour 4 --every 72000`. Until now DRS read a source only when someone opened a report whose snapshot had expired, at whatever hour that was.
- [admin] **`--live-runs window` keeps readers to the window**: outside it a reader gets the newest data kept, however old, with a line saying when the report reads its data, and no Refresh button; nothing a reader does then reads the source. `always` (the default) reads it as before. The schedules and their last run are on Monitoring → Logs → Report schedules; each run is in the access log as user `scheduler`. Details: `docs/report-schedule.md`.
- A report kept to its run window says so above the table: "This report reads its data only at set times (...). Showing the data read at ...".

## 1.11.0 — 2026-11-26
- [admin] **Mirror copies on PostgreSQL are `UNLOGGED` tables** (`[mirror] unlogged = true`, the default): a full copy deletes and inserts every row of its source, and every one of those rows went through the WAL - archived and backed up with the whole cluster (on 2026-10-28 the mirrors were about 70-98% of the DRS server's WAL, some 22 GB a day for a 4 GB mirror schema). An unlogged table writes no WAL and is not in a base backup. The cost: after a crash (or an immediate shutdown) PostgreSQL empties these tables; each mirror's next run fills its copy again (an incremental mirror that finds no row of its source in the copy makes a full copy instead of trusting its watermark), or `python -m drscore mirror run <code>` at once. Existing copies are switched at their next run; `unlogged = false` keeps ordinary tables. SQLite and SQL Server are unchanged. Details: `docs/mirror.md`.

## 1.10.0 — 2026-11-26
- **A new look, from dbabrain.dev's shared site kit (kit 1.7.0)**: Manrope type with Vietnamese, an indigo accent, softer corners, light / dark following the device or the switch in the top bar. The left menu is light in light mode; each item has **its icon on a coloured tile**, each report group its own colour, which the home page repeats on the group's card.
- The sign-in page and the reports opened by a link take the new look too.
- [admin] **The admin pages share the portal's frame**: the same left menu and the same top bar (page title, VN / EN, light / dark, account, a real sign-out). Their own dark menu and the "Logout" button that signed nobody out are gone.
- [admin] **The menu is grouped**: **Administration** - Admin home, Report setup (groups, reports, parameters, grid columns, row filters), Datasources, Users and access (users, roles, attributes, the three grants), Sharing and notices (public links, Telegram / Zalo, the AI agent); **Monitoring** - Feedback, Logs (search the logs, activity, access, audit, AI questions, notices sent, snapshot history, the feedback table). The admin home (/admin/) shows those groups as cards.
- [admin] Superset (DRS BI) takes the portal's colours, type, corners and logo, light and dark.
- [designer] **New manual: from several SQL files to one finished dashboard** (`docs/dashboard-from-sql.md`): preparing the SQL (`:name` binds read as text with defaults, no `:name` in comments), one report per file (parameters, test, columns, publish, run once), the charts in Superset (which chart for which kind of view), layout, tabs and colours (`label_colors`), attaching the dashboard to a host report that reads the other reports' datasets, links without sign-in (grant the host and every member; a report with people's names behind a sign-in link), and what to do when the dashboard does not show. Two lessons: a stacked area layer at 0 still draws its outline (use `NULLIF(SUM(...), 0)`); a KPI tile's "vs day before" misleads after a holiday. Written from a real build: 8 SQL files, 8 reports, 17 charts, 2 tabs.

## 1.9.0 — 2026-11-11
- **Arrange the table's columns**: drag a column's header to move it; hide or show columns with the **Columns** button (tick / untick) or a column's menu (⋮), which also **pins it to the left** while you scroll. Your browser remembers the layout, the sort and the page size for each report; **Default layout** in the Columns button puts the report's own back.
- **XLSX / CSV / TXT follow the table as you arranged it**: the columns you show, in their order on screen - still every row. Columns the report's design does not export stay out.

## 1.8.0 — 2026-11-11
- [admin] **Public links may be shown inside another site** (an iframe): per link, on System → Public links, the sites allowed - `*` for any site, or sites such as `https://hrms.example.com` - set when the link is made or changed later in its row (`publiclink add|set --frame`). Empty keeps the portal only, as before; every other page keeps refusing other sites' frames.

## 1.7.2 — 2026-11-09
- [admin] **System → Public links**: make a report link on a page - the report, the buttons (export, refresh), who may open it (anybody with the address, or signed-in readers), what the reader may change, title, expiry - and switch links off / on or between public and sign-in; the address is shown once. No command line needed.
- [admin] A default link account, `user_public` (`[public] default_user`), is made at start-up with no password: a link made for it gives it that report, with the buttons chosen.
- [admin] The activity log names a link's runs and exports, and the changes made on Public links.

## 1.7.1 — 2026-11-09
- Report links that show a table (opened without signing in) now show **the report page itself, without the menu**: the same form (a list stays a list, with its codes and names), the same buttons - **Refresh data, Print / Save PDF, XLSX / CSV / TXT with every row** - and the same table with a filter on each column, sorting and pages.
- [admin] What a link's reader may use follows the grant of the user the link runs as (`--as`): export and refresh, as in the portal; the form shows only the parameters the link lets them change.

## 1.7.0 — 2026-11-09
- **Feedback** (left menu): report a bug, suggest an idea or ask the administrators from the page you are on, with screenshots or files (images, PDF, XLSX, DOCX, CSV, TXT). The administrators answer in each feedback; you see its status (new, seen, in progress, done, won't fix), write again to reopen it, and press "I checked" once it is fixed.
- **Typhon**, the AI agent (left menu, when the administrators turn it on): ask which report shows what, how to fill a report's parameters, how to use the portal. Typhon sees only what your account may see and changes nothing.
- The version at the bottom of the left menu: click it to see what is new; a new version shows "What's new" once by itself.
- **A new look**, after petweb: the left menu in sections (Tools, System), foldable to its icons; the top bar carries the page's title, VN / EN, light / dark and the account; a floating **Feedback** button on every page. Made for desktop, iPad (portrait and landscape) and phones: on a phone the menu slides in from ☰, long tables become cards, dialogs take the whole screen.
- [admin] Menu **System → Administration** (as petweb's): **Activity log** (filter by date, account, action, refused / failed only, search; three tabs Actions, Report views, Data changes; **Export Excel**), **Feedback** as a table, **AI agent** (overview: state, cost this month / budget, tokens today and this month; by account, by day, by model; questions; settings), **Telegram / Zalo notices** (which channel is ready, send a test, the notices sent), and the admin pages.
- [admin] **Activity log** (Admin → Logs): every request a user makes - each change with what was sent (never a password, token or key), each page opened, each refused or failed request - on the design pages, BI pages, public links and admin pages too.
- [admin] Feedback: see them all (a red count of new ones on the menu), answer, set the status, kind, urgency and fixed-in version. Typhon reads a new feedback first: a reply to the sender and a note for the administrators.
- [admin] Notices to a **Telegram group and a Zalo group**: new feedback, the sender writing again or reopening, a report waiting for approval, Typhon's monthly budget reached. Each channel runs only with both its group (`chat_id`) and its bot token.
  - The AI key and bot tokens are kept **encrypted** in the DRS database with `DRS_SECRET_KEY` (`python -m drscore secret set ...`), never in a file; `notify status`, `notify test`, `ai status` say what is missing.
  - New log pages: AI agent questions (question, answer, tokens, cost), Notices sent.
- [admin] New settings in `app.toml`: `[activity]`, `[feedback]`, `[ai]`, `[notify]` (`docs/feedback-typhon-notices.md`). Migration 0019 (tables added only). Needs the `anthropic` package (the image must install from PyPI).

- [admin] Typhon: who may ask is set per account (`[ai] allowed_users`) or per role (`allowed_roles`); a report it runs stops above `max_report_rows` rows (10 000 by default) and Typhon asks for narrower parameters instead.

## 1.6.4 — 2026-11-09
- The portal opens at an IP address (http) and a domain (https behind a reverse proxy) at once; dashboards show at both.
- [admin] `[app] other_urls` and `[bi.superset] site_urls`; the admin pages keep their styles at the domain; `bi sync` lets every portal address embed the dashboards.

## 1.6.3 — 2026-10-26
- Report links: the parameters a reader may change show the value in use, dates as date pickers.

## 1.6.2 — 2026-10-26
- A report link may be not public: it asks for a sign-in first, then shows the report with the reader's own rights.

## 1.6.1 — 2026-10-26
- [admin] Sign-in length per account (`user set-session`): a view-only account can stay signed in 30 days.

## 1.6.0 — 2026-10-24
- Choice lists: the person who decides ticks the choices on a page opened by a link, and the reports use the ticked list at once.

## 1.5.1 — 2026-10-22
- Public links show the report's HTML layout, refreshed by themselves.
- [admin] `publiclink set` changes what a link shows and keeps its address.

## 1.5.0 — 2026-10-22
- Public links: a report opened without signing in (a wall screen, a reader without an account).
- [admin] Mirrors: source tables copied into the DRS database on a schedule, so heavy reports read the copy, not the production source.

## 1.4.2 — 2026-10-15
- Status badges readable again.

## 1.4.1 — 2026-10-15
- [admin] A Superset account per DRS user.

## 1.4.0 — 2026-10-15
- A report's dashboard can hold charts on other reports' data, each with its own parameters.

## 1.3.1 — 2026-10-10
- [designer] The BI page lists the Superset dashboards again.

## 1.3.0 — 2026-10-10
- [designer] Self-service reports and dashboards; "Can publish" publishes without approval.

## 1.2.0 — 2026-10-10
- [designer] The report designer role: write, test and propose reports, approved by an administrator.

## 1.1.1 — 2026-10-08
- System improvements and small fixes.

## 1.1.0 — 2026-10-08
- System improvements and small fixes.

## 1.0.0 — 2026-10-07
- The first release of the `drscore` package: reports written in SQL, shown as grids, HTML layouts and BI dashboards.
