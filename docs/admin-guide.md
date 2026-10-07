# DRS administration guide

This guide grows with each milestone. Milestone 8 added the admin pages: everything below that
is done with SQL can now also be done there.

## Admin pages

Administrators get an "Administration" entry in the portal menu, `/admin/`. Everything an
administrator maintains has a page, in Vietnamese or English (the portal's language). The button
"Back to the portal" under the title leads back to the home page; signing out is in the portal's
user menu.

| Menu | Pages | Extra buttons |
| --- | --- | --- |
| Reports | report groups, reports, parameters, grid columns, row filters | report: **Test run** (runs it now with the default parameters, as you, and shows the row count or the error; it cannot choose a value, so for a required parameter without a default it says so), **Open report** (to the report's own page, where the parameters are chosen), **Check** (the findings of `report validate`), **Create BI table** (runs it with the default parameters and writes the result to the table named in "BI dataset table", so a Superset dashboard can be designed on it - what `cache warm --report CODE` does) |
| Datasources | every connection setting, "New password" (stored encrypted, never shown) | **Test connection** |
| Users and access | users ("New password", ends their sessions; "Must change password": the user's next sign-in opens only the account page until they have chosen a new one), user attributes, roles and members, group grants, report grants | user: **Unlock** (after too many wrong passwords) |
| Logs | access log (who opened / ran / exported what), audit log (every change), snapshot history | read-only |

Every save is checked like the CLI checks it (codes, parameter names, default-value tokens, grid
formats, datasource settings, password policy, one of user / role on a grant); a refused save shows
the reason on the form and changes nothing. Saving a report also shows the warnings of `report
validate`. Every create / update / delete is written to `drs_audit_log` with the values before and
after (password hashes and datasource secrets masked).

Security: only users with `is_admin` get in (others get 403, a signed-out visitor goes to the sign
-in page); the pages use the portal's session. A form post or button that does not come from a page
of the same site (Origin / Referer check) is refused with 403, so another site cannot drive an
administrator's browser.

## Move reports between databases: metadata export / import

```bash
python -m drs metadata export --out reports.json                 # everything
python -m drs metadata export --out emp.json --report EMP_LIST   # one report and what it needs
python -m drs metadata import --in reports.json --dry-run        # show what would change
python -m drs metadata import --in reports.json
```

The file (JSON) holds datasources, report groups, reports with their parameters, columns and row
filters, users, user attributes, roles with members, and grants - rows refer to each other by code,
so ids may differ. It never holds password hashes or encrypted datasource passwords: a datasource
arrives without its password (the import says which ones need `datasource set-password`; an
`env:NAME` reference is kept), users arrive without a password.

Import matches by code: existing rows are updated, missing ones created, nothing else is deleted -
except that the parameters, columns and row filters of an imported report become exactly those of
the file. An import never makes an existing user an administrator. It is one transaction (any error:
nothing imported), and every row created or changed is in the audit log (action `IMPORT`).

Typical use: design reports on SQLite, then `export` there, switch `config/database.toml` to
PostgreSQL (`db upgrade`), `import`, `datasource set-password` for each source, `report validate`.

## Add, change and remove a report with SQL only

No code change and no restart: the next click uses the new rows.

```sql
-- A new report, visible to everyone who holds the group. It opens on its grid; report_type
-- (GRID / HTML / BI) is only the tab the page opens on, GRID when left out.
INSERT INTO drs_report
    (report_code, report_name, group_id, report_type, datasource_id, query_text, cache_ttl_seconds)
SELECT 'EMP_BY_DEPT', 'Employees by department', g.group_id, 'GRID', d.datasource_id,
       'SELECT department, COUNT(*) AS employees FROM employee WHERE company_code = :company GROUP BY department',
       300
FROM drs_report_group g, drs_datasource d
WHERE g.group_code = 'HR' AND d.datasource_code = 'DEMO';

INSERT INTO drs_report_param (report_id, param_name, label, data_type, is_required)
SELECT report_id, 'company', 'Company', 'text', true FROM drs_report WHERE report_code = 'EMP_BY_DEPT';
```

Every `:name` in `query_text` must be a row of `drs_report_param`. Values are always bound, never
pasted into the SQL. On PostgreSQL the `drs` schema is first in the search path, so unqualified
table names work.

- Hide a report: `UPDATE drs_report SET is_active = false WHERE report_code = '...';`
- Remove it: `DELETE FROM drs_report WHERE report_code = '...';` - its parameters, columns, row
  filters, grants and current snapshots go with it. Snapshots already moved to
  `drs_report_snapshot_history` stay.
- Check definitions: `python -m drs report validate`.

Useful columns of `drs_report`: `sort_order` (decimal: 1.5 goes between 1 and 2), `is_active`,
`grid_enabled` (false hides the grid and its exports), `html_design_uri`, `bi_design_uri`.

**DRS shows what the query returns.** The report writer decides the columns and the value of every
cell in the SQL or the procedure; DRS adds nothing of its own. Without rows in `drs_report_column`
the grid shows every column under the name the query gives it, and a number exactly as returned
(`14969547`, not `14,969,547`). A header text, thousands separators, a width or a footer total
appear only where the writer asked for them: an alias in the query, or a `drs_report_column` row
with `label`, `display_format`, `footer_aggregate`. A date is shown in the portal's date format.

## Parameters

One row of `drs_report_param` per parameter. `data_type` is `text`, `int`, `decimal`, `date`,
`datetime` or `bool`; `input_kind` is `input`, `select` or `multiselect`; `sort_order` is a decimal;
`is_active = false` hides a parameter without deleting it (the query must not use it then).

An option of a `select` / `multiselect` has two fields: `value`, which the system binds to the
query and stores (`-1`), and `label`, which the form shows (`Tất cả`):
`options_json = [{"value": "-1", "label": "Tất cả"}, {"value": "0", "label": "..."}]`, or an
`options_query` returning two columns, value then label. The label is text for the person
choosing; do not repeat the value in it.

**A list that depends on another parameter** (a company, then the units of that company): the
options query of the second parameter uses the first one as a bind, by its name:

```sql
-- parameter company_id (select):        SELECT CompanyId, CompanyName FROM org.Company
-- parameter business_unit_id (select):  SELECT BusinessUnitId, BusinessUnitName FROM org.BusinessUnit
--                                        WHERE CompanyId = :company_id AND LevelNumber = 2
```

The parameter it uses must come before it (`sort_order`); `report validate` / **Check** says so
otherwise. On the report page the second list is filled again as soon as the first one changes
(htmx asks `GET /api/reports/CODE/params/NAME/options`), also along a chain of three or more, and
what was chosen stays chosen when it is still in the new list. While nothing is chosen in the first
one the query runs with NULL: `CompanyId = :company_id` then gives an empty list and no error;
write `(:company_id IS NULL OR CompanyId = :company_id)` to show everything instead. When the
report runs, a value that is not in the list of the chosen company is refused like any other value
that is not an option.

`default_value` fills the form when the report is opened. One text column for every type:

| Write | Gives |
| --- | --- |
| `A1A`, `100`, `1.5`, `2026-01-31`, `2026-01-31 08:00`, `true` | that value, in the parameter's type |
| `A1A,B2B` | several values of a multiselect |
| `@today`, `@yesterday`, `@week_start`, `@month_start`, `@month_end`, `@prev_month_start`, `@prev_month_end`, `@year_start`, `@year_end` | that day (date / datetime parameters) |
| `@now` | the current date and time |
| `@today-7d`, `@month_start-1m`, `@today+2w`, `@year_start-1y` | a day before / after, in days `d`, weeks `w`, months `m`, years `y` |
| `@user.company_code` | the viewer's own value of that user attribute (none for `*`) |
| `@@text` | the literal `@text` |

## Who looked at what

`drs_access_log` has one row per look at a report: `OPEN` (page opened, also refused attempts),
`RUN` (the source was read), `VIEW` (shown from a snapshot: cache, another tab, the HTML layout, a
dashboard), `EXPORT_XLSX` / `EXPORT_CSV` / `EXPORT_TXT`, plus `LOGIN` / `LOGOUT`. Each row has the
time (UTC), user, report, tab (`view_key`), parameters, snapshot, cache hit, rows, duration, status
(`OK` / `DENIED` / `ERROR`) with the error, client IP, browser (`user_agent`) and `request_id` (the
same id as in `runtime/logs/drs.log`).

```sql
SELECT logged_at, username, action, report_code, view_key, status, row_count, params_json
FROM drs_access_log WHERE report_code = 'SALARY_LIST' ORDER BY logged_at DESC;
```

## Cache and retention

`drs_report.cache_ttl_seconds` is the retention of a report's result. While it is valid every
user gets the cached result; once it has expired, the next user who opens the report gets fresh
data, and the old result moves to `drs_report_snapshot_history`. `0` means every open reads the
source. A user with `can_refresh` can force a fresh read.

History is never deleted by DRS. To see what a report showed in the past:

```sql
SELECT snapshot_id, created_at, archived_at, archive_reason, row_count, params_json
FROM drs_report_snapshot_history WHERE report_code = 'EMP_LIST' ORDER BY created_at DESC;
```

The history grows with every expired result; watch its size on reports with a short retention and
large results.

## HTML designs

An HTML report shows a Jinja2 template from the `designs/` folder (`[designs] root` in
`app.toml`). Register it with `UPDATE drs_report SET design_uri = 'file:html/<file>.html' ...`.
The file is read again when it changes - no restart.

The template runs in a sandbox with auto-escaping: it can only use these values, and cannot read
files, query a database or run Python.

| Name | Content |
| --- | --- |
| `report` | `code`, `name`, `description` |
| `params` | the parameter values, by name (`params.from_date`) |
| `columns` | list of `name`, `label`, `type` |
| `rows` | the rows **this user** may see, as dictionaries (`r.company_code`) |
| `row_count` | number of rows |
| `snapshot` | `created_at`, `expires_at`, `is_stale` |
| `user` | `username`, `display_name` |
| `now` | current time |
| `csp_nonce` | put it on your own scripts: `<script nonce="{{ csp_nonce }}">` |

Filters: `fmt_number(decimals)` (12,345.68), `fmt_date` (31/12/2026), `fmt_datetime`
(31/12/2026 17:45), `tojson` (safe inside a script), plus Jinja2's standard ones. `static('...')`
gives the address of a library served by the portal, for example
`static('vendor/echarts/echarts.min.js')` or `static('vendor/tabler/tabler.min.css')`.

Rules: a script without the nonce, or any file from another site, is blocked by the browser. A
name that does not exist is an error (`DESIGN_INVALID`, the line is shown to administrators); test
optional values with `{% if params.company is defined %}`. Pass data to a chart with
`var rows = {{ rows | tojson }};`, never by pasting values into the script.

## Known limits

- A row edited by hand in the DRS database is not recorded in `drs_audit_log`. Only changes made
  through the CLI, the admin pages and `metadata import` are audited.
- Admin toasts (results of Test run / Test connection) stay until closed: they are shown without a
  script, because the portal allows no inline script.
