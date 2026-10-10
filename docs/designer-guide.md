# DRS report designer guide

This guide covers the **report designer** role introduced in DRS 1.2.0 and expanded in DRS 1.3.0
with self-service publishing and BI dashboard integration. Report designers can compose, test, and
propose reports directly within the DRS portal, publish them directly when granted publishing
rights, and attach Superset dashboards without administrative intervention.

## What a designer can and cannot do

A report designer is a non-administrative user who has been granted design privileges on one or
more report groups and query access to one or more datasources.

Proposals are stored as drafts in `drs_report_draft`. When a designer holds the **Can publish**
right on the group, tested drafts can be published directly to live tables (`drs_report`,
`drs_report_param`, `drs_report_column`). Otherwise, live report definitions are modified only when
an administrator reviews and approves the draft.

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
- **Publish reports directly** without administrator approval when granted **Can publish** on the
  target group (and current group for modifications).
- Submit tested drafts for administrator review when lacking publishing rights, or withdraw
  pending drafts back to draft status.
- **Manage BI dashboards** on the BI page (`/design/reports/{code}/bi`): explore datasets in
  Superset, attach compatible dashboards, detach dashboards, and trigger synchronization on demand.

### What a designer cannot do
- **Cannot publish without the publish right**: Designers without **Can publish** on the report's
  group must submit drafts for administrator review. When changing an existing report, publishing
  requires **Can publish** on both the report's current group and its target group.
- **Cannot touch restricted reports**: Reports marked as restricted (`is_restricted = true`) are
  strictly administrator-only (such as payroll or executive compensation data). Designers cannot
  view or propose changes to restricted reports, and restricted datasets are never added to
  designer Superset roles.
- **Cannot view datasource connection details**: Designers never see database server hostnames,
  ports, usernames, passwords, or driver connection strings. They only see the datasource code
  and name.
- **Cannot attach invalid dashboards**: A dashboard cannot be attached if it uses datasets other
  than the report's dataset, or if the report has row filters configured (row filters are not
  supported in embedded mode).
- **Cannot alter administrative properties**: Designers cannot change:
  - Active status (`is_active`)
  - Restricted flag (`is_restricted`)
  - Default tab / opens-on type (`report_type`)
  - Grid view toggle (`grid_enabled`)
  - HTML design template URI (`html_design_uri`)
  - Query timeout (`timeout_seconds`)
  - Maximum row limit (`max_rows`)
  - Serve stale data on error (`serve_stale_on_error`)
  - Report sort order (`sort_order`)
  - Row filters (`drs_report_row_filter`)
  - Report access grants (`drs_grant_report`)

When a new report is published or approved, it is created active, opens on `GRID`, unrestricted,
with `created_by` set to the designer. In self-service mode, its `bi_dataset_table` is automatically
assigned as `report_code.lower()`. The report is immediately visible to everyone holding a grant on
its group.

## Set-up by an administrator

### Requirements for designer status
A user is recognized as a report designer when they have:
1. At least one report group grant with **Can design** enabled (`drs_grant_group.can_design = true`).
2. At least one datasource grant (`drs_grant_datasource`).

To allow a designer to publish reports without approval, enable **Can publish** on their group grant:
3. **Can publish** enabled on the group grant (`drs_grant_group.can_publish = true`). Note that
   `can_publish` requires `can_design`; granting `can_publish` without `can_design` gives no access.

Administrators (`is_admin = true`) automatically possess full designer and publisher rights across
all active groups and datasources.

Once granted, the portal navigation bar displays the **Report design** link (`/design`). For
administrators, this menu item displays a badge with the count of pending drafts waiting for review.

### Configuring design and publish access in the Admin UI
Administrators configure design access under `/admin/` in the **Users and access** section:
1. **Group grants**: Create or edit a grant for a user or role on the target report group. Check
   **Can design** to allow drafting; check **Can publish** to allow direct publishing without approval.
2. **Datasource grants**: Create a grant linking the target datasource to the user or role.

### Configuring design access with the CLI
Administrators can also manage design and publishing grants using the command line:

```bash
# Grant datasource access to a user or role
python -m drscore grant datasource SALES_DB --user john
python -m drscore grant datasource SALES_DB --role ANALYSTS

# Grant group access with design rights (requires approval to publish)
python -m drscore grant group SALES --user john --design

# Grant group access with design and direct publishing rights (no approval needed)
python -m drscore grant group SALES --user john --design --publish
python -m drscore grant group SALES --role ANALYSTS --design --publish

# Revoke publishing rights while keeping design rights
python -m drscore grant group SALES --user john --no-publish

# Revoke datasource or group access
python -m drscore revoke datasource SALES_DB --user john
python -m drscore revoke group SALES --user john

# View a user's permissions, design rights, and publishing rights
python -m drscore grant show --user john
```

The output of `grant show` lists report grants as well as design and publish rights:

```text
GROUP        REPORT                   TYPE  EXPORT  REFRESH  VIA
SALES        MONTHLY_SALES            GRID  yes     no       user john
1 report(s) visible to john.
Design groups: SALES
Publish groups: SALES
Datasources: SALES_DB
```

### Upgrading an existing database
Upgrading an existing database requires applying database migrations:

```bash
python -m drscore db upgrade
```

- Migration `0009_report_designer`: adds `drs_grant_group.can_design`, `drs_grant_datasource`, and `drs_report_draft`.
- Migration `0010_can_publish`: adds `drs_grant_group.can_publish`.

## Workflow

### 1. Start a proposal
Navigate to `/design` (**Report design** in the top navigation). The page displays:
- **My drafts**: Drafts created by the user, showing report code, name, status, and last update time.
- **Reports I can change**: Live active reports in groups the user may design in. When self-service is
  enabled, each report includes a **Dashboard** link leading directly to its BI management page
  (`/design/reports/{code}/bi`).

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
Before a draft can be submitted or published, it must pass a test run:
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

### 5. Publish or submit for approval
Once a draft passes testing (`tested_hash == content_hash`), the action available depends on the
designer's privileges on the report group:

#### Direct publishing (with `Can publish`)
If the designer has **Can publish** enabled on the report's group:
- The editor displays the **Publish** button alongside an optional review note field.
- For an existing report change, the designer must hold **Can publish** on both the target group and
  the report's current group.
- Clicking **Publish** (`POST /design/drafts/{id}/publish`) applies the draft directly to the live
  report tables (`drs_report`, `drs_report_param`, `drs_report_column`) in a single transaction.
- Status is set to `APPROVED`, reviewed_by is recorded as the designer, and audit action
  `DRAFT_PUBLISH` is logged with the applied content.
- In self-service mode, DRS automatically assigns `bi_dataset_table = report_code.lower()` (with an
  incremental suffix `_2`, `_3` if another report already uses that name) and launches background
  preparation of the BI dataset (`prepare_dataset`).
- The report is live immediately without administrative review!

#### Submitting for approval (without `Can publish`)
If the designer lacks publishing rights on the group:
- The editor displays **Submit** to send the draft for administrative review.
- If you edit any field, parameter, or SQL text after testing, the test fingerprint is invalidated
  and you must run **Save & test** again before submitting.
- Submitting sets the draft status to `PENDING` and records `DRAFT_SUBMIT` in the audit log.
- An administrator reviews and approves or rejects the draft under `/design`.

### 6. Withdraw or delete
- **Withdraw**: The author can click **Withdraw** on a `PENDING` draft to return it to `DRAFT` status
  for further edits.
- **Delete**: Authors can delete drafts in `DRAFT` or `REJECTED` status. Administrators can delete
  any draft that is not approved.

## The BI page and Superset self-service

When self-service is turned on (`[bi.superset] self_service = true`), designers who can edit a report
can manage its Superset integration directly via the BI page (`/design/reports/{code}/bi`).
Users who can edit the report but lack the **Can publish** right on its group view this page in
read-only mode.

### What the BI page provides
- **BI dataset information**: Shows the report's dataset table name (`bi_dataset_table`) and Superset
  dataset status. If the dataset view has not been prepared yet, it shows *not made yet - run the report once*
  with a direct link to the report.
- **Superset designer role**: Shows the exact Superset role name (`DRS_DESIGN_<GROUP_CODE>`) that
  dashboard authors need in Superset to access this dataset.
- **Open in Superset**: A button/link (`/design/reports/{code}/superset`) that opens Superset's Explore
  view for this dataset in a new browser tab, allowing designers to begin creating charts immediately.
- **Dashboards on this dataset**: Discovers dashboards in Superset built against this dataset:
  - **Usable dashboards**: Dashboards where every dataset used is this report's dataset. Shows a
    **Use for this report** button (`POST /design/reports/{code}/bi/link`). Clicking this enables
    embedding in Superset (with allowed domain matching DRS `base_url`) and attaches the dashboard
    to the report (`bi_design_uri = superset:<uuid>`).
  - **Unusable dashboards**: If a dashboard uses other datasets, it cannot be attached and displays
    the reason (e.g. *Dashboard uses 1 other dataset(s)*).
- **Current dashboard**: If a dashboard is currently attached, shows its details with a **Remove**
  button (`POST /design/reports/{code}/bi/unlink`) to detach it.
- **Sync now**: A button (`POST /design/reports/{code}/bi/sync`) that runs `sync_report` inline to
  immediately create or find the dataset in Superset, refresh its column schema, and ensure the
  group's role has access permissions. The outcome is displayed immediately on the page.

> [!NOTE]
> Self-service BI is supported on **PostgreSQL** DRS databases only (which provide schema views in
> `drs_bi`). On SQLite installations, the BI page indicates that self-service is not available on SQLite.

> [!WARNING]
> Reports with row filters (`drs_report_row_filter`) cannot have embedded dashboards attached because
> Superset guest tokens do not yet carry row rules for DRS row filters. The BI page will refuse
> attaching a dashboard if row filters exist on the report.

## Links to Superset across DRS

DRS provides quick links to Superset across the application so designers and administrators can
navigate directly to datasets and dashboards:
- **Designer editor**: Quick access to dataset exploration during report editing.
- **Report list (`/design`)**: In the "Reports I can change" section, each report displays a
  **Dashboard** link to `/design/reports/{code}/bi` when self-service is enabled.
- **Report page Design menu**: For users with edit permissions, the **Design** dropdown menu on
  the report page (`/reports/{code}`) includes:
  - **BI settings**: Navigates to `/design/reports/{code}/bi`.
  - **Open in Superset**: Redirects to Superset's Explore view for the report's dataset
    (`/design/reports/{code}/superset`).
  - **Dashboard in Superset**: When a dashboard is attached, opens the live dashboard directly in
    Superset (`/design/reports/{code}/superset/dashboard`).
- **Admin report page (`/admin/report/`)**: Administrators can click the **Open in Superset** action
  button on any report.

All Superset links route through DRS redirect endpoints (`/design/reports/{code}/superset` and
`/design/reports/{code}/superset/dashboard`). DRS queries Superset dynamically and redirects the
browser, ensuring that DRS portal pages never wait for Superset to load or fail if Superset is
temporarily slow.

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
