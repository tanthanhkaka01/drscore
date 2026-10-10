# DRS report designer guide

This guide covers the **report designer** role introduced in DRS 1.2.0. Report designers can
compose, test, and propose reports directly within the DRS portal, while administrators review
and approve all changes before they reach live report tables.

## What a designer can and cannot do

A report designer is a non-administrative user who has been granted design privileges on one or
more report groups and query access to one or more datasources.

Proposals are stored as drafts in `drs_report_draft`. Live report definitions (`drs_report`,
`drs_report_param`, `drs_report_column`) are never modified until an administrator reviews and
approves the draft.

### What a designer can do
- Propose new reports in granted report groups.
- Propose changes to existing, non-restricted reports in granted report groups.
- Write and test SQL queries against granted datasources.
- Configure report metadata: code (for new reports), name, description, group, datasource, and
  retention cache TTL (`cache_ttl_seconds`).
- Define parameters: names, labels, data types, input kinds, default values, options queries,
  and advanced constraints.
- Define result grid columns: labels, formatting, widths, alignments, visibility, export flags,
  and footer aggregations.
- Automatically populate column definitions from query test results.
- Submit tested drafts for administrator review, or withdraw pending drafts back to draft status.

### What a designer cannot do
- **Cannot publish changes directly**: All new reports and modifications require administrator
  approval before taking effect.
- **Cannot touch restricted reports**: Reports marked as restricted (`is_restricted = true`) are
  strictly administrator-only (such as payroll or executive compensation data). Designers cannot
  view or propose changes to restricted reports.
- **Cannot view datasource connection details**: Designers never see database server hostnames,
  ports, usernames, passwords, or driver connection strings. They only see the datasource code
  and name.
- **Cannot alter administrative properties**: Designers cannot change:
  - Active status (`is_active`)
  - Restricted flag (`is_restricted`)
  - Default tab / opens-on type (`report_type`)
  - Grid view toggle (`grid_enabled`)
  - HTML design template URI (`html_design_uri`)
  - BI dashboard URI (`bi_design_uri`)
  - BI dataset table name (`bi_dataset_table`)
  - Query timeout (`timeout_seconds`)
  - Maximum row limit (`max_rows`)
  - Serve stale data on error (`serve_stale_on_error`)
  - Report sort order (`sort_order`)
  - Row filters (`drs_report_row_filter`)
  - Report access grants (`drs_grant_report`)

When an administrator approves a new report, it is created active, opens on `GRID`, unrestricted,
with `created_by` set to the designer. It is immediately visible to everyone holding a grant on its
group.

## Set-up by an administrator

### Requirements for designer status
A user is recognized as a report designer when they have:
1. At least one report group grant with **Can design** enabled (`drs_grant_group.can_design = true`).
2. At least one datasource grant (`drs_grant_datasource`).

Administrators (`is_admin = true`) automatically possess full designer rights across all active
groups and datasources.

Once granted, the portal navigation bar displays the **Report design** link (`/design`). For
administrators, this menu item displays a badge with the count of pending drafts waiting for review.

### Configuring design access in the Admin UI
Administrators configure design access under `/admin/` in the **Users and access** section:
1. **Group grants**: Create or edit a grant for a user or role on the target report group, and
   check the **Can design** checkbox.
2. **Datasource grants**: Create a grant linking the target datasource to the user or role.

### Configuring design access with the CLI
Administrators can also manage design grants using the command line:

```bash
# Grant datasource access to a user or role
python -m drscore grant datasource SALES_DB --user john
python -m drscore grant datasource SALES_DB --role ANALYSTS

# Grant group access with design rights
python -m drscore grant group SALES --user john --design
python -m drscore grant group SALES --role ANALYSTS --design

# Revoke datasource or group access
python -m drscore revoke datasource SALES_DB --user john
python -m drscore revoke group SALES --user john

# View a user's permissions and design rights
python -m drscore grant show --user john
```

The output of `grant show` lists report grants as well as design rights:

```text
GROUP        REPORT                   TYPE  EXPORT  REFRESH  VIA
SALES        MONTHLY_SALES            GRID  yes     no       user john
1 report(s) visible to john.
Design groups: SALES
Datasources: SALES_DB
```

### Upgrading an existing database
Upgrading to DRS 1.2.0 requires applying migration `0009_report_designer`:

```bash
python -m drscore db upgrade
```

This migration adds `drs_grant_group.can_design`, creates the `drs_grant_datasource` table, and
creates the `drs_report_draft` table.

## Workflow

### 1. Start a proposal
Navigate to `/design` (**Report design** in the top navigation). The page displays:
- **My drafts**: Drafts created by the user, showing report code, name, status, and last update time.
- **Reports I can change**: Live active reports in groups the user may design in.

To start:
- Click **New report** to draft a brand new report.
- Click **Propose a change** next to an existing report to create a draft pre-filled with the live
  report's current definition.

Only one open draft (`DRAFT` or `PENDING`) can exist per report code at any time.

### 2. Edit the report definition
The editor form includes:
- **Report code**: Upper-case identifier (`^[A-Z][A-Z0-9_]{1,49}$`). Read-only when modifying an
  existing report.
- **Report name**: User-friendly report title (up to 200 characters).
- **Group**: Report group dropdown (only groups where the designer has design rights).
- **Datasource**: Target database dropdown (only granted datasources).
- **Retention (s)**: Cache time-to-live in seconds (`cache_ttl_seconds`, default 0).
- **Description**: Explanatory text (up to 2000 characters).
- **SQL / procedure**: The query text. Named parameters must be prefixed with a colon (e.g.,
  `:dept_code`). Every bind must match an active parameter.
- **Parameters table**: Input definitions.
- **Columns table**: Grid display definitions.

Click **Save** to store changes without testing.

### 3. Save & test
Before a draft can be submitted, it must pass a test run:
1. In the **Test parameters** section below the editor, enter sample values for all active
   parameters (multiselect parameters accept comma-separated values).
2. Click **Save & test**.

The system saves the draft and executes the query directly against the target datasource:
- Test queries use the configured application timeout (`limits.default_timeout_seconds`) and max
  row limit (`limits.default_max_rows`).
- Shows up to the first 200 rows of output.
- **No cache, no snapshot, no locks, no BI dataset tables, and no row filters** are applied during
  test runs.
- Test runs are recorded in `drs_audit_log` with action `DRAFT_TEST`.

**Error reporting**:
- If the SQL query contains syntax errors, invalid references, or times out, the database driver's
  technical error message is displayed so the designer can fix the query.
- Connection errors (`DATASOURCE_UNAVAILABLE`) show only a generic message without exposing internal
  hostnames or connection details.

A successful test calculates a SHA-256 fingerprint (`tested_hash`) of the draft content and records
the execution summary.

### 4. Fill columns from test
After a successful test run, click **Fill columns from test**. The editor inspects the columns
returned by the datasource and appends any missing columns to the **Columns** table, setting default
labels and data types.

### 5. Submit for approval
Click **Submit** to send the draft for review.
- The button is enabled only after a successful test run on the current content
  (`tested_hash == content_hash`).
- If you edit any field, parameter, or SQL text after testing, the test fingerprint is invalidated
  and you must run **Save & test** again before submitting.
- Submitting sets the draft status to `PENDING` and records `DRAFT_SUBMIT` in the audit log.

### 6. Withdraw or delete
- **Withdraw**: The author can click **Withdraw** on a `PENDING` draft to return it to `DRAFT` status
  for further edits.
- **Delete**: Authors can delete drafts in `DRAFT` or `REJECTED` status. Administrators can delete
  any draft that is not approved.

## Parameters and columns

### Parameter fields
| Field | Description |
| --- | --- |
| `Active` | Whether the parameter is active. Query text can only bind active parameters. |
| `Name in SQL` | Parameter bind name in SQL (`:name`). Must match `^[a-z][a-z0-9_]*$`. |
| `Label` | Label displayed on the parameter form. |
| `Type` | Data type: `text`, `int`, `decimal`, `date`, `datetime`, `bool`. |
| `Input` | Control type: `input`, `select`, `multiselect`. |
| `Required` | Whether the user must provide a value (checked by default). |
| `Default value` | Default value or dynamic token (`@today`, `@month_start`, `@user.attr`). |
| `Options query` | SQL query returning `value` and `label` columns for select inputs. Can bind preceding parameters for cascading dropdowns. |
| `Options datasource` | Datasource for the options query (defaults to the report datasource). Must be an authorized datasource. |
| `Advanced` | JSON object for less common settings (see below). |

### Parameter "Advanced (JSON)" field
Each parameter row has an **Advanced** input accepting a JSON object for additional settings:
- `options_json`: Static list of options. Example: `{"options_json": ["A", "B"]}` or
  `{"options_json": [{"value": "1", "label": "One"}, {"value": "2", "label": "Two"}]}`.
- `multi_bind_mode`: Binding mode for multiselect parameters: `"expand"` (default) or `"csv"`.
- `min_value` / `max_value`: Value bounds (numbers or dates as strings). Example:
  `{"min_value": "1", "max_value": "100"}`.
- `max_length`: Maximum allowed string length. Example: `{"max_length": 50}`.
- `regex`: Validation regular expression pattern. Example: `{"regex": "^[A-Z0-9]{4}$"}`.

Example:
```json
{"options_json": ["Active", "Inactive"], "multi_bind_mode": "expand"}
```

### Column fields
| Field | Description |
| --- | --- |
| `Result column` | Field name matching the query result column. |
| `Label` | Display header in the grid table. |
| `Type` | Column data type (`text`, `int`, `decimal`, `float`, `bool`, `date`, `datetime`, `time`). |
| `Format` | Display formatting string (`number:2`, `percent:1`, `date`, `datetime`, `time`). |
| `Width (px)` | Fixed column width in pixels. |
| `Align` | Text alignment: `left`, `center`, `right`. |
| Flags | Checkboxes for `Visible`, `Exported`, `Sortable`, `Filterable`, `Frozen`. |
| `Footer` | Summary aggregate function: `sum`, `avg`, `count`, `min`, `max`. |

## Review and approval

Administrators review pending drafts under `/design` (or by following the pending drafts badge in
the navigation bar).

When viewing a `PENDING` draft, administrators see a **Review panel**:
- **Field changes**: Table comparing live values with proposed values (name, description, group,
  datasource, retention).
- **Parameters diff**: Lists added, removed, and modified parameter names.
- **Columns diff**: Lists added, removed, and modified column names.
- **Query diff**: Unified line diff of the SQL query.
- **Last test**: Timestamp, duration, and row count of the author's test run.

### Approving a draft
1. Enter an optional review note.
2. Click **Approve**.

Approval takes place in a single transaction:
- **New report**: Inserts the new `Report` row (active, `GRID`, not restricted,
  `created_by = designer`, `updated_by = admin`).
- **Existing report**: Updates `report_name`, `description`, `group_id`, `datasource_id`,
  `query_text`, `cache_ttl_seconds`, and `updated_by`. Non-designer settings remain untouched.
- **Parameters and columns**: Replaced to match the draft definition exactly.
- **Audit**: Logged as `CREATE` or `UPDATE` on `drs_report` and `DRAFT_APPROVE` on `drs_report_draft`.
- Validation checks (`check_report`) run automatically, flashing any warnings to the administrator.

> [!IMPORTANT]
> A newly approved report is visible immediately to all users holding a grant on its group. Verify
> the group assignment before approving.

### Rejecting a draft
1. Enter a mandatory review note explaining what needs correction.
2. Click **Reject**.

The draft status changes to `REJECTED`, and the note is logged (`DRAFT_REJECT`). The author sees
the rejection reason when viewing the draft. Saving edits to a rejected draft automatically resets
its status to `DRAFT` so the author can re-test and re-submit.

## Security

### Read-only datasource accounts
The SQL written by a designer executes using the datasource's configured database account.
Administrators must ensure that datasource accounts are strictly read-only:
- **Microsoft SQL Server**: Use a database user assigned only the `db_datareader` role on the target
  database, or grant explicit `SELECT` permissions on specific schemas and views.
- **PostgreSQL**: Assign a role with `SELECT` privileges only on allowed tables/schemas.
- **Oracle**: Grant `CREATE SESSION` and `SELECT` on designated tables or views only.

### Test runs bypass row filters
Test queries run directly against the database without applying user row-filter rules
(`drs_report_row_filter`). Test previews show actual data (up to 200 rows). Never grant a datasource
to a user who should not be allowed to see raw data from that source.

### Protection of credentials and restricted reports
- Designers never see database credentials, passwords, server hostnames, or ports.
- Restricted reports (`is_restricted = true`) cannot be seen or modified by designers.
- All draft actions (`DRAFT_CREATE`, `DRAFT_UPDATE`, `DRAFT_TEST`, `DRAFT_SUBMIT`, `DRAFT_WITHDRAW`,
  `DRAFT_DELETE`, `DRAFT_APPROVE`, `DRAFT_REJECT`) are written to `drs_audit_log` with actor and
  timestamp.
