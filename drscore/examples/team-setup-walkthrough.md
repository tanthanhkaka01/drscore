# Team setup walkthrough: Self-service reports and Apache Superset

This walkthrough guides you through configuring a complete team workspace from scratch across
DRS (`http://drs.example.local:8090`) and Apache Superset (`http://bi.example.local:8088`).

In DRS 1.3.0, the system operates on a **dynamic self-service** model: after an administrator performs
a one-time configuration of datasources, report groups, users, and roles, **the administrator does
nothing per report**. Team designers create, test, and publish reports directly, author BI dashboards
in Superset on automatically provisioned datasets, and attach them to live reports via the self-service
BI page.

## Environment and naming conventions

In compliance with project standards (decision 79 in `docs/decisions.md`):
- **DRS Portal**: `http://drs.example.local:8090`
- **Superset**: `http://bi.example.local:8088`
- **Datasource**: `MSSQL_SALES_RO` (SQL Server, host `sql01.example.local`, database `SalesDB`, login `drs_sales_ro`)
- **Report Group**: `SALES`
- **DRS Role**: `SALES_STAFF`
- **DRS Users**: `sales_viewer` (views reports), `sales_designer` (drafts and publishes reports)
- **Superset User**: `sales_bi` (designs dashboards in Superset)
- **Superset Role**: `DRS_DESIGN_SALES` (group design role created and managed by DRS)
- **Passwords**: Never write a password in commands or files; enter it at the prompt or form.

---

## Part 1: Set up once (administrator)

The administrator completes this initial setup once. Afterwards, the team authors and publishes
reports independently.

### 1. Register the datasource

Datasource codes must match `^[A-Z][A-Z0-9_]{1,49}$` (upper-case letters, digits, and underscores).
The database account `drs_sales_ro` must be strictly read-only on SQL Server (`db_datareader` or
explicit `SELECT` grants on allowed tables and views) because report designers execute queries
directly with it.

DRS encrypts the datasource password using Fernet (`DRS_SECRET_KEY`) and stores it in
`drs_datasource.secret_ref`.

- **Admin UI**: In `/admin/` under **Datasources > Datasources** (`/admin/datasource/`), click **Create Datasource**:
  - **Code**: `MSSQL_SALES_RO`
  - **Name**: `Sales Database (RO)`
  - **Type**: `mssql`
  - **Server**: `sql01.example.local`
  - **Database / service**: `SalesDB`
  - **Authentication**: `password`
  - **User**: `drs_sales_ro`
  - **New password**: Enter the password at the hidden prompt / form.
  - **Active**: Checked.
- **CLI**:
  ```bash
  python -m drscore datasource add MSSQL_SALES_RO --type mssql --host sql01.example.local --database SalesDB --user drs_sales_ro --description "Sales database (read-only)"
  ```

Test connectivity using the **Test connection** button in the Admin UI or via CLI:
```bash
python -m drscore datasource test MSSQL_SALES_RO
```

### 2. Create the report group

Report group codes must match `^[A-Z][A-Z0-9_]{0,49}$`.

- **Admin UI**: In `/admin/` under **Reports > Report groups** (`/admin/reportgroup/`), click **Create Report group**:
  - **Code**: `SALES`
  - **Name**: `Sales`
  - **Description**: `Sales team reports and operational dashboards`
  - **Active**: Checked.

### 3. Create DRS role and users

Role codes are automatically converted to upper-case.

- **Admin UI**:
  - **Role**: Under **Users and access > Roles** (`/admin/role/`), click **Create Role**:
    - **Code**: `SALES_STAFF`
    - **Name**: `Sales Staff`
    - **Active**: Checked.
  - **Users**: Under **Users and access > Users** (`/admin/user/`), click **Create User** for each user:
    - User 1: **User name**: `sales_viewer`, **Name**: `Sales Viewer`, **Active**: Checked, **Roles**: `SALES_STAFF`.
    - User 2: **User name**: `sales_designer`, **Name**: `Sales Designer`, **Active**: Checked, **Roles**: `SALES_STAFF`.
- **CLI**:
  ```bash
  python -m drscore role add SALES_STAFF --name "Sales Staff"
  python -m drscore user add sales_viewer --display-name "Sales Viewer"
  python -m drscore user add sales_designer --display-name "Sales Designer"
  python -m drscore role member add SALES_STAFF sales_viewer
  python -m drscore role member add SALES_STAFF sales_designer
  ```

### 4. Configure DRS grants

Configure viewing and publishing access:
1. **Viewers (SALES_STAFF)**: Grant group `SALES` to role `SALES_STAFF` with export enabled.
2. **Designer (sales_designer)**: Grant group `SALES` to user `sales_designer` with both `--design`
   and `--publish`, plus a datasource grant on `MSSQL_SALES_RO`.

- **Admin UI**:
  - In **Users and access > Group grants** (`/admin/grantgroup/`):
    - Grant 1: Group `SALES`, Role `SALES_STAFF`, **Can export** checked.
    - Grant 2: Group `SALES`, User `sales_designer`, **Can design** checked, **Can publish** checked, **Can export** checked.
  - In **Users and access > Datasource grants** (`/admin/grantdatasource/`):
    - Datasource `MSSQL_SALES_RO`, User `sales_designer`.
- **CLI**:
  ```bash
  # Viewers access
  python -m drscore grant group SALES --role SALES_STAFF

  # Designer authoring and publishing access
  python -m drscore grant group SALES --user sales_designer --design --publish
  python -m drscore grant datasource MSSQL_SALES_RO --user sales_designer
  ```

Verify permissions and rights using `grant show`:
```bash
python -m drscore grant show --user sales_designer
```
Output:
```text
GROUP        REPORT                   TYPE  EXPORT  REFRESH  VIA
1 report(s) visible to sales_designer.
Design groups: SALES
Publish groups: SALES
Datasources: SALES_DB
```

### 5. Configure Superset integration and admin account

DRS drives Superset via its REST API using an administrator account. Configure `config/app.toml`:

```toml
[bi.superset]
enabled = true
base_url = "http://bi.example.local:8088"    # as browsers reach Superset
api_url = "http://bi.example.local:8088"     # as the DRS server reaches Superset
username = "drs_service"                     # guest token account
password_env = "DRS_SUPERSET_PASSWORD"
guest_token_ttl_seconds = 300
check_design = true

# Self-service keys (1.3.0)
self_service = true                          # DRS automates datasets, roles, and embedding
admin_username = "admin"                     # Superset account with Admin role
admin_password_env = "DRS_SUPERSET_ADMIN_PASSWORD"
database_name = "DRS"                        # connection name in Superset pointing to PostgreSQL
design_role_prefix = "DRS_DESIGN_"           # prefix for group roles: DRS_DESIGN_<GROUP_CODE>
```

Export `DRS_SUPERSET_ADMIN_PASSWORD` on the DRS server.

In Superset (**Settings > Database Connections**), ensure the database connection named `DRS` exists,
pointing to the PostgreSQL database with read permissions on schema `drs_bi`.

### 6. Synchronize group role in Superset

Run the CLI command once to pre-create the team's design role in Superset:
```bash
python -m drscore bi sync --group SALES
```
This contacts Superset and creates role `DRS_DESIGN_SALES`.

### 7. Create Superset dashboard designer user

In Superset (`http://bi.example.local:8088`):
1. Navigate to **Settings > List Users** and click **+**:
   - **Username**: `sales_bi`
   - **Active**: Checked
   - **Roles**: Assign `Gamma` and `DRS_DESIGN_SALES`
   - **Password**: Set securely.

> [!WARNING]
> Dashboard designers need `Gamma` + `DRS_DESIGN_<GROUP>` only. Never assign `Alpha` or `sql_lab`,
> as those roles expose all datasets and database schemas. The role `DRS_Embedded` is reserved for
> guest tokens and must never be assigned to a human user.

---

## Part 2: Every report (the users, no administrator)

From this point on, the administrator does nothing per report. The team authors reports, creates
datasets, designs dashboards, and attaches them autonomously.

### 1. Author and publish the report (sales_designer)

1. Sign in to DRS (`http://drs.example.local:8090`) as `sales_designer`.
2. Navigate to **Report design** (`/design`) and click **New report**:
   - **Report code**: `SALES_ORDERS`
   - **Report name**: `Sales Orders`
   - **Group**: `SALES`
   - **Datasource**: `MSSQL_SALES_RO`
   - **Retention (s)**: `300`
   - **SQL query**:
     ```sql
     SELECT order_id, order_date, customer_name, total_amount, status
     FROM sales_order
     WHERE order_date >= :from_date
     ```
3. In the **Parameters** table, configure `:from_date`:
   - **Label**: `From Date`
   - **Type**: `date`
   - **Input**: `input`
   - **Required**: Checked
   - **Default value**: `@month_start`
4. Under **Test parameters**, enter a test date (e.g. `2026-01-01`) and click **Save & test**. The test
   runs directly on `MSSQL_SALES_RO`, displays output rows, and computes the test fingerprint (`tested_hash`).
5. Click **Fill columns from test** to automatically generate grid column definitions.
6. Because `sales_designer` has the **Can publish** privilege on group `SALES`, the editor displays
   the **Publish** button.
7. Click **Publish**.

The report is immediately live! DRS automatically assigns `bi_dataset_table = sales_orders` and
starts a background thread to prepare the BI dataset view.

### 2. Dataset availability in Superset

When the report is run for the first time (via background preparation or by clicking **Run** on its
report page), DRS creates the PostgreSQL view `drs_bi.sales_orders`.

DRS then synchronizes automatically with Superset:
- Creates dataset `drs_bi.sales_orders` on database connection `DRS`.
- Refreshes column schema in Superset.
- Grants permission `datasource access on [DRS].[sales_orders]` to role `DRS_DESIGN_SALES`.

### 3. Design the dashboard in Superset (sales_bi)

1. In DRS, click **Open in Superset** from:
   - The report page **Design** menu (`/reports/SALES_ORDERS`)
   - The BI settings page (`/design/reports/SALES_ORDERS/bi`)
   - The report editor under `/design`
2. Sign in to Superset as `sales_bi`.
3. Because `sales_bi` has role `DRS_DESIGN_SALES`, the dataset `sales_orders` is accessible.
4. Create charts on `sales_orders` and arrange them into a dashboard named "Sales Performance Dashboard".
   *(Snapshot note: While designing in Superset, all cached snapshots are visible. You can temporarily
   filter on `drs_snapshot_id` or test with a single cached result).*
5. Save the dashboard.

### 4. Attach the dashboard in DRS (sales_designer)

1. In DRS, open the report's BI settings page:
   - Via the report page **Design > BI settings** menu, or
   - In `/design`, click **Dashboard** next to `SALES_ORDERS` in the "Reports I can change" list, or
   - Directly navigate to `/design/reports/SALES_ORDERS/bi`.
2. The BI page queries Superset and displays the "Sales Performance Dashboard" under **Dashboards on this dataset**.
3. Because all charts in the dashboard use only `sales_orders`, the dashboard is marked usable with a
   **Use for this report** button.
4. Click **Use for this report**:
   - DRS automatically enables dashboard embedding in Superset with allowed domain `http://drs.example.local:8090`.
   - DRS stores `bi_design_uri = superset:<uuid>`.
   - The BI page updates to show the attached dashboard with a **Remove** button if detaching is needed later.

### 5. Viewers access the dashboard (sales_viewer)

1. `sales_viewer` signs in to DRS and opens `/reports/SALES_ORDERS`.
2. The page displays both a **Grid** tab and a **Dashboard** tab.
3. Clicking the **Dashboard** tab renders the embedded Superset dashboard.
4. DRS transparently requests a guest token scoped strictly to `sales_viewer`'s chosen parameters
   and snapshot (`drs_snapshot_id`), providing full data isolation without requiring `sales_viewer` to
   have a Superset account.

---

## Who can do what

| Action | What grants it | Where configured |
| --- | --- | --- |
| **View report** | Group grant or Report grant to user/role | `/admin/grantgroup/` or `/admin/grantreport/` |
| **Export data** | `can_export = true` on Group or Report grant | `/admin/grantgroup/` or `/admin/grantreport/` |
| **Refresh cache** | `can_refresh = true` on Group or Report grant | `/admin/grantgroup/` or `/admin/grantreport/` |
| **Draft report** | Group grant with `can_design` AND Datasource grant | `/admin/grantgroup/` and `/admin/grantdatasource/` |
| **Publish report directly** | Group grant with `can_design` AND `can_publish` AND Datasource grant | `/admin/grantgroup/` and `/admin/grantdatasource/` |
| **Approve report** | Administrator status (`is_admin = true`) (for designers without `can_publish`) | `/admin/user/` |
| **Design BI dashboard** | Superset user with `Gamma` + `DRS_DESIGN_<GROUP>` | Superset: **Settings > List Users** |
| **Attach / detach dashboard** | Group grant with `can_publish` via BI page (`/design/reports/{code}/bi`) | `/design/reports/{code}/bi` |
| **Sync Superset dataset/roles** | Group grant with `can_publish` (BI page "Sync now") or Admin (CLI `bi sync`) | BI page or CLI |
| **Administer** | Administrator status (`is_admin = true`) | `/admin/user/` |

---

## Troubleshooting

- **Lower-case code refused**: Codes for datasources, groups, and reports must match `^[A-Z][A-Z0-9_]{1,49}$`.
  Attempting to save `sales_orders` as a report code is refused with validation error. Use `SALES_ORDERS`.
- **Designer sees no "Publish" button**: The direct **Publish** button appears only when the user has both
  `can_design` and `can_publish` on the group and the current draft has passed testing (`tested_hash == content_hash`).
  Verify permissions with `python -m drscore grant show --user <username>`.
- **The BI page says "dataset not made yet - run the report once"**: The report query has not run yet to
  create the PostgreSQL view `drs_bi.<table_name>`. Open the report, select parameters, and click **Run**,
  or click **Sync now** on the BI page.
- **Superset user sees no dataset**: Ensure `sales_bi` is marked **Active** in Superset and has role
  `DRS_DESIGN_SALES`. Run `python -m drscore bi sync --group SALES` or click **Sync now** on the BI page
  to verify that dataset permissions have been assigned to the role.
- **Attaching dashboard is refused: "Dashboard uses N other dataset(s)"**: All charts in the dashboard
  must use only the report's dataset. Embedded guest tokens enforce `drs_snapshot_id` across every dataset
  in the dashboard; multiple datasets cannot be scoped safely.
- **Attaching dashboard is refused: row filters**: Reports with row filters (`drs_report_row_filter`) cannot
  be embedded because guest tokens do not support row-filter rules. Remove the row filters or use grid/HTML views.
- **BI engine unavailable**: If DRS displays `BI_ENGINE_UNAVAILABLE`, check that Superset is running,
  `DRS_SUPERSET_ADMIN_PASSWORD` is set in the environment, and the database connection `DRS` exists in Superset.
- **The BI page says "not available on SQLite"**: Self-service Superset integration requires a PostgreSQL
  DRS database (with schema `drs_bi`). On SQLite, self-service operations are disabled.
