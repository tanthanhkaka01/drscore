# DRS user guide - report designer

This guide explains how report designers create, test, and publish reports in DRS, and how to attach Apache Superset BI dashboards. DRS runs offline on your internal network without requiring internet access.

## Links

| What | Where |
| --- | --- |
| DRS portal | `<DRS portal link>` (Report design: `<DRS portal link>/design`) |
| Apache Superset (with your Superset account) | `<Superset link>` |

## What you can and cannot do

As a report designer, you have self-service capabilities within your granted report groups and datasources.

### What you can do
- Draft new reports in report groups where you hold design rights.
- Propose changes to existing, non-restricted reports in your design groups.
- Write and test SQL queries against datasources granted to you.
- Configure report metadata: code, title, group, datasource, and cache retention TTL.
- Define parameters, default values, options queries, and cascading dependencies.
- Populate grid columns automatically from test query results, and configure formatting and totals.
- Publish reports directly to the portal if you have the **Can publish** privilege on the group.
- Submit drafts for administrator review if you lack direct publishing rights.
- Manage BI dashboards on the BI page and attach dashboards to reports.

### What you cannot do
- View database server hostnames, ports, usernames, passwords, or connection strings.
- View, edit, or propose changes to restricted reports (`is_restricted = true`), which are reserved for administrators.
- Configure row filters (`drs_report_row_filter`) or report access grants (`drs_grant_report`).
- Alter administrative properties such as report active status, query timeout, or maximum row limit.

## Create a GRID report

Follow these steps to create and publish a new report:

### 1. Start a new draft
Click **Report design** in the left menu (`/design`). The page displays "My drafts" and "Reports I can change". Click **New report** to open the draft editor.

### 2. Configure report header fields
- **Report code**: Enter an identifier in UPPER CASE only (`^[A-Z][A-Z0-9_]{1,49}$`, e.g. `SALES_SUMMARY`). Lower-case letters are refused.
- **Report name**: Enter a clear display name for the portal menu.
- **Group**: Select a report group where you hold design rights.
- **Datasource**: Select an authorized datasource to query.
- **Retention (s)**: Set cache retention in seconds. A value of `300` keeps query results cached for 5 minutes. Enter `0` if the report must query live data on every run.

### 3. Write the SQL query
Enter your SQL query in the query editor. Use named parameter binds prefixed with a colon (e.g. `:from_date`, `:schema_name`). Every bind in the query must correspond to an active parameter in the Parameters table. Parameters are safely bound by database drivers rather than concatenated as strings, preventing SQL injection vulnerabilities.

### 4. Define parameters
In the **Parameters** table, configure each parameter required by your query:
- **Name in SQL**: The bind name without the colon (e.g. `from_date`).
- **Label**: Display text shown to the user on the parameter form.
- **Type**: Data type (`text`, `int`, `decimal`, `date`, `datetime`, `bool`).
- **Input**: Control type (`input`, `select`, `multiselect`).
- **Required**: Check if the user must provide a value before running the report.
- **Default value**: Enter a default literal value or dynamic token (e.g. `@today`, `@month_start`, `@month_end`, `@year_start`).
- **Options query**: For select inputs, a query whose first column is the value and whose optional second column is the label shown.
- **Dependent select**: An options query can use an earlier parameter as a bind to create dependent dropdowns. The referenced parameter must appear earlier in the parameter list. For example, to list tables for a chosen schema:
  ```sql
  SELECT table_name, table_name FROM information_schema.tables WHERE table_schema = :schema_name
  ```
- **Advanced (JSON)**: Optional JSON configuration for static option lists, value bounds, or regex patterns, e.g. `{"options_json": ["A", "B"]}` or `{"min_value": "1", "max_value": "100"}`.

### 5. Save & test
Under **Test parameters**, enter sample test values for all active parameters and click **Save & test**.
DRS executes the query directly against the datasource using the datasource's read-only database credentials.
The test runs with configured system timeouts and displays the first 200 rows of output without creating a cached snapshot.
A successful test records a fingerprint (`tested_hash`) required for submission or publishing.

### 6. Fill columns from test
Click **Fill columns from test** to automatically populate the **Columns** table from query result fields.
Configure column display settings:
- **Label**: User-friendly header name.
- **Format**: Display formatting string, such as `number:2` for decimals, `percent:1`, or `date`.
- **Align and width**: Text alignment (`left`, `center`, `right`) and fixed column width in pixels.
- **Footer**: Summary aggregates, such as `sum`, `avg`, `count`, `min`, or `max`.

### 7. Publish or submit for approval
- **Direct publishing**: If you hold **Can publish** on the report group, the **Publish** button is available. Clicking **Publish** makes the report live immediately without administrator approval.
- **Submit for approval**: If you lack publish rights, click **Submit for approval**. The draft moves to `PENDING` status for administrator review.
- **Note on testing**: If you edit any field, parameter, or SQL text after testing, the test fingerprint is invalidated. You must run **Save & test** again before publishing or submitting.

## Change an existing report

To update an existing report:

1. In **Report design** (`/design`), locate the report under "Reports I can change", or open the report page and select **Design > Propose a change**.

2. Click **Propose a change** to create a draft pre-filled with the live report's current configuration. Only one open draft (`DRAFT` or `PENDING`) can exist per report at a time.

3. Edit the query, parameters, or columns as needed.

4. Run **Save & test** with sample parameter values to verify your modifications.

5. Click **Publish** (if you have **Can publish**) or click **Submit for approval** to send the draft for administrator review.

6. If a pending draft needs further edits, click **Withdraw** to return it to `DRAFT` status.

## Attach a BI dashboard

After publishing a report, you can attach an Apache Superset dashboard:

1. **Run the report once**: Open the published report and click **Run**. The first run creates the report's BI dataset, and DRS adds it to Superset by itself (publishing already does this in the background when every required parameter has a default). If the dataset still does not show in Superset, click **Sync now** on the BI page (`/design/reports/{code}/bi`).

2. **Open in Superset**: On the BI page, click **Open in Superset** to open the dataset in Superset's Explore view in a new browser tab.

3. **Build charts**: Author charts in Superset using your Superset account. The dataset holds every cached run of the report. While exploring, you may filter on `drs_snapshot_id` to look at one run, but remove that filter before you save: on the report page, DRS limits the dashboard to the run on screen by itself.

4. **Assemble dashboard**: Build and save a dashboard in Superset that queries only this report's dataset.

5. **Attach in DRS**: Return to the DRS BI page (`/design/reports/{code}/bi`), locate the dashboard in the list, and click **Use for this report**.

6. **View dashboard**: The report page immediately displays a **Dashboard** tab. Portal viewers can interact with the dashboard without needing Superset accounts. Click **Dashboard in Superset** from the report page anytime to open the dashboard directly in Superset.

> [!IMPORTANT]
> **Dashboard attachment rules**
> - The dashboard must use ONLY this report's dataset. Dashboards querying multiple datasets cannot be attached.
> - The report must NOT have row filters configured (row-level security is not supported in embedded dashboards).
> - You must hold the **Can publish** right on the report group to attach or detach dashboards.

## Hands-on walkthrough: from a new report to its dashboard

This walkthrough follows a real session on a DRS server, step by step. It builds the report `DB_TABLE_CATALOG` (the tables and views of a SQL Server database) with three linked parameters, then a Superset dashboard with a bar chart, a pie chart, number tiles and a data grid. The names below are examples: use your own group, datasource and accounts.

**Before you start**, you need:
- A DRS account with design rights on a report group. Here: user `designer`, group **DEMO**, with **Can publish**.
- A SQL Server datasource granted to you. Here: `MSSQL_SALES_RO`.
- A Superset account that your administrator linked to your DRS user. Here: `designer_bi`.

### Step 1 - Sign in

1. Open the DRS portal.
2. Optional: click **English** under the sign-in form to use the English interface.
3. Enter your user name and password, then click **Sign in**.

You see **Home**: your report groups, each with the reports you can open.

### Step 2 - Start a new report

1. Click **Report design** in the left menu. You see **My drafts** and **Reports I can change**.
2. Click **New report** (top right).
3. Fill in the header fields:

| Field | Value used |
| --- | --- |
| Report code | `DB_TABLE_CATALOG` |
| Report name | `Table catalog by schema` |
| Retention (seconds) | `300` (results are cached for 5 minutes) |
| Report group | `DEMO` |
| Data source | `MSSQL_SALES_RO` |
| Description | `Tables and views of the sales database with their column counts` |

4. Paste the query into **SQL query**:

```sql
SELECT t.TABLE_SCHEMA AS table_schema,
       t.TABLE_NAME   AS table_name,
       t.TABLE_TYPE   AS table_type,
       COUNT(c.COLUMN_NAME) AS column_count
FROM INFORMATION_SCHEMA.TABLES t
LEFT JOIN INFORMATION_SCHEMA.COLUMNS c
  ON c.TABLE_SCHEMA = t.TABLE_SCHEMA AND c.TABLE_NAME = t.TABLE_NAME
WHERE (:table_type  IS NULL OR t.TABLE_TYPE   = :table_type)
  AND (:schema_name IS NULL OR t.TABLE_SCHEMA = :schema_name)
  AND (:table_name  IS NULL OR t.TABLE_NAME   = :table_name)
GROUP BY t.TABLE_SCHEMA, t.TABLE_NAME, t.TABLE_TYPE
ORDER BY t.TABLE_SCHEMA, t.TABLE_NAME
```

> [!TIP]
> Each `:name` is a parameter. The pattern `(:name IS NULL OR column = :name)` means "All" when the user leaves the parameter empty.

### Step 3 - Add three linked parameters

1. In the **Parameters** card, click **Add parameter** three times.
2. Fill in the three rows. Set **Data type** to `text` and **Input kind** to `select`, and untick **Required** on each row:

| # | Parameter name | Label | Its list depends on |
| --- | --- | --- | --- |
| 1 | `table_type` | Object type | - |
| 2 | `schema_name` | Schema | parameter 1 |
| 3 | `table_name` | Table | parameter 2 (and 1) |

3. Enter the **Options query** for each row. Leave **Options data source** on *(report datasource)*.

```sql
-- 1. table_type
SELECT DISTINCT TABLE_TYPE FROM INFORMATION_SCHEMA.TABLES ORDER BY TABLE_TYPE

-- 2. schema_name: uses :table_type, so its list follows parameter 1
SELECT DISTINCT TABLE_SCHEMA FROM INFORMATION_SCHEMA.TABLES
WHERE (:table_type IS NULL OR TABLE_TYPE = :table_type) ORDER BY TABLE_SCHEMA

-- 3. table_name: uses :schema_name, so its list follows parameter 2
SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES
WHERE (:table_type IS NULL OR TABLE_TYPE = :table_type)
  AND (:schema_name IS NULL OR TABLE_SCHEMA = :schema_name) ORDER BY TABLE_NAME
```

> [!NOTE]
> An options query can only use parameters that are listed **above** it.

4. Click **Create draft** at the bottom of the page. The draft opens; a **Test parameters** card now shows one box per parameter.

### Step 4 - Test, set the columns, publish

1. Click **Save & test**. Leave **Test parameters** empty to test with "All". You see **Test query succeeded**, the row count and the time, and the first rows of the result.
2. Click **Fill columns from test**. You see "Columns filled from test results." and one row per result column in the **Columns** card.
3. Give each column a clear label and format:

| Field name | Label | Format | Width (px) | Alignment | Footer aggregate |
| --- | --- | --- | --- | --- | --- |
| `table_schema` | Schema | | 160 | | |
| `table_name` | Table | | 320 | | |
| `table_type` | Object type | | 140 | center | |
| `column_count` | Columns | `number:0` | 110 | right | `sum` |

4. Click **Save & test** again. Any change after a test needs a new test.
5. Click **Publish** (bottom of the page). If you see only **Submit for approval**, you lack **Can publish**; submit the draft and wait for an administrator.

You see "Report published successfully. The BI dataset is being prepared." The draft is now **Approved** and read-only.

### Step 5 - Run the report

1. Open the report from the left menu (**DEMO > Table catalog by schema**).
2. Leave the parameters at **- All -** and click **Run report**. You see the grid with the four columns, a **Columns** total in the footer, and the export buttons (XLSX, CSV, TXT).
3. Try the linked lists: choose **Object type = VIEW**. The **Schema** list now shows only the schemas that contain views, and the **Table** list only those views.

### Step 6 - Open the dataset in Superset

1. On the report page, click **Design > BI settings**.
2. Check that the status is **Dataset ready in Superset**. If not, click **Sync now**. The page also shows **Your Superset account**.
3. Click **Open in Superset**. Superset opens in a new tab.
4. Sign in with your **Superset** account (not your DRS account).

### Step 7 - Build the charts

Superset opens **Explore** on the dataset `drs_bi.db_table_catalog`: its metric `COUNT(*)` and its columns are in the left panel. Every chart follows the same pattern:

1. Click a chart type icon at the top of the **Data** tab (line, bar, area, table, big number `4k`, pie), or **View all charts**.
2. Drag fields from the left panel (**Metrics**, **Columns**) into the boxes.
3. Click **Create chart** (or **Update chart**) to preview.
4. Type the chart name at the top, then click **Save**.

> [!IMPORTANT]
> Do not save a filter on `drs_snapshot_id`. DRS shows each user only the run on their screen.

**7.1 Bar chart: "Top 10 tables by column count"**
- Chart type: **Bar Chart**.
- **X-axis**: `table_name`.
- **Metrics**: `column_count` (Superset proposes `SUM`; click **Save** in the pop-up).
- **X-Axis Sort By**: `SUM(column_count)`, and untick **X-Axis Sort Ascending**.
- **Sort query by**: `column_count` (`SUM`), **Sort Descending** ticked.
- **Row limit**: `10`.
- **Customize** tab: **Rotate x axis label** = `45°`.

When you save the **first** chart, the **Save chart** window opens: type the chart name, and type a new dashboard name in **Add to dashboard** (here `Table catalog`). Superset shows "A new dashboard will be created". Click **Save**. For the other charts, pick the same dashboard from the list.

**7.2 Pie chart: "Tables by object type"**
- Chart type: **Pie Chart**.
- **Dimensions**: `table_type`.
- **Metric**: `COUNT(*)`.

**7.3 Number tiles: "Total tables" and "Total columns"**
- Chart type: **Big Number** (the `4k` icon).
- **Metric**: `COUNT(*)` for "Total tables".
- Make a second Big Number with **Metric** `column_count` (`SUM`) for "Total columns".

**7.4 Data grid: "Table list"**
- Chart type: **Table**.
- **Query mode**: **Raw records**.
- **Columns**: `table_schema`, `table_name`, `table_type`, `column_count`.

### Step 8 - Arrange and publish the dashboard

1. In Superset, click **Dashboards > Table catalog**.
2. Click **Edit dashboard**. Drag a chart to move it; drag its right edge to resize it. A good layout:
   - Row 1: Total tables, Total columns, Tables by object type.
   - Row 2: Top 10 tables by column count, Table list.
3. Click **Save**.
4. Click the **Draft** badge next to the title so that it reads **Published**.

> [!NOTE]
> In Superset itself the charts count every cached run of the report. On the DRS report page they show only the run on screen.

### Step 9 - Attach the dashboard to the report

1. Go back to the DRS **BI settings** page and refresh it.
2. In the **Dashboard** card, the dashboard appears with status **Usable**. Click **Use for this report**. You see "Dashboard attached to report."
3. Open the report. It now has a **Dashboard** tab next to **Grid**, with the five charts. Viewers do not need a Superset account.
4. The dashboard follows the parameters. Choose **Object type = VIEW** and click **Run report**: the tiles count only the views, and every chart shows views only.

## Troubleshooting

Refer to this table to resolve common issues during report design:

| Problem | Cause | Solution |
| --- | --- | --- |
| Code refused | Invalid code format | Report codes must use upper-case letters, numbers, and underscores only (`^[A-Z][A-Z0-9_]{1,49}$`). |
| "The data source cannot be reached" | Database connection failure | The datasource cannot be reached by the server. Contact your administrator to verify database availability. |
| "The data source cannot be reached" on a new SQL Server datasource | The server uses a self-signed TLS certificate | Ask your administrator to tick **Trust server certificate** on the datasource, then run **Save & test** again. |
| "parameter ... is not an active parameter" | Missing parameter definition | A colon bind `:param` in SQL has no matching row in the Parameters table or its Active checkbox is unticked. |
| Submit for approval / Publish refused | Draft modified after testing | Any edit after a test invalidates the test fingerprint. Click **Save & test** again before submitting or publishing. |
| Dataset not in Superset yet | The report has not run yet | Run the report once from its portal page, then click **Sync now** on the BI page if needed. |
| Dashboard not offered on BI page | Multi-dataset dashboard | The Superset dashboard queries datasets other than this report's dataset. Ensure it queries only this report. |
