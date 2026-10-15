# DRS user guide - administrator

This guide explains how administrators configure and maintain DRS. DRS runs offline on local servers or workstations within your internal network, installed from local files. No internet access is required.

## Links

| What | Where |
| --- | --- |
| DRS portal | `<DRS portal link>` (administration: `<DRS portal link>/admin/`) |
| Apache Superset | `<Superset link>` |

## Administration pages

Sign in at `/login` with an administrative account and click **Administration** in the left menu, or go to `/admin/`. The administration area provides four menu groups:

- **Reports**
  Manage report groups, individual reports, parameters, grid columns, and row filters.
  Each report includes action buttons:
  - **Open report**: Opens the live report page where parameters are selected.
  - **Test run**: Runs the report immediately with default parameters and reports row counts or errors.
  - **Check**: Inspects definitions and displays validation findings from `report validate`.
  - **Open in Superset**: Opens the report's dataset directly in Apache Superset.

- **Datasources**
  Configure database connections, hostnames, ports, and encrypted credentials.
  Use the **Test connection** action to verify reachability.

- **Users and access**
  Manage user accounts, password policies, user attributes, roles, and memberships.
  Configure group grants, individual report grants, and datasource grants.

- **Logs**
  Review operational and security activity across the portal:
  - **Access log**: Records who opened, viewed, ran, or exported each report.
  - **Audit log**: Tracks every create, update, and delete action across system settings.
  - **Snapshot history**: Displays past cached results and archival reasons.

## One-time set-up

Follow these five steps to set up a new reporting workspace:

### 1. Register a datasource

Navigate to **Datasources > Datasources** and click **Create Datasource**.

- **Code**: Enter a code in UPPER CASE only (`^[A-Z][A-Z0-9_]{1,49}$`). Lower-case letters are strictly refused. Example: `MSSQL_SALES_RO`.
- **Connection settings**: Specify the type (`mssql`, `oracle`, `postgresql`, `sqlite`), server host, port, database name, and username.
- **New password**: Enter the database password. DRS stores it encrypted and never displays it again. Leave this field empty on future edits to keep the existing password.
- **Read-only login**: Ensure the database account has strictly read-only access on the source database (e.g. `db_datareader` on SQL Server or `SELECT` privileges on PostgreSQL). This is essential because report designers execute queries directly using this login.
- **Test connection**: After saving, run the **Test connection** action on the datasource to verify the connection.
- **Trust server certificate**: If connecting to an older SQL Server that rejects encrypted connections or uses a self-signed certificate, tick **Trust server certificate**.

### 2. Create a report group

Navigate to **Reports > Report groups** and click **Create Report group**.

- **Code**: Enter a group code in UPPER CASE (such as `SALES`).
- **Name and description**: Provide a clear display name and purpose description.
- **Organization**: Report groups organize reports in the portal navigation and define boundaries for design permissions and BI dataset roles.
- **Active**: Check to enable the group.

### 3. Create users, roles, and attributes

Navigate to **Users and access**:

- **Users**: Create user accounts and set an initial password in **New password**.
  Tick **Must change password** to require the user to choose a new password at their first sign-in. Until then, every page sends the user to the Account page (`/account`); other requests are refused with 403.
- **Roles and members**: Create functional roles (e.g. `SALES_STAFF`) and assign user accounts as members.
- **User attributes**: Define key-value attributes for row filters (e.g. `company_code = HQ`). Enter `*` to allow access to every value. User attributes are matched automatically against report row-level filter rules.

### 4. Configure grants

Under **Users and access**, configure permissions:

- **Group grants**: Grant access to an entire report group for a user or role. Check **Can export** (download XLSX / CSV / TXT), **Can refresh** (force fresh data queries), **Can design** (create and edit drafts), and **Can publish** (publish directly without approval).
- **Report grants**: Grant access to an individual report. This is required for restricted reports (`is_restricted = true`), which are hidden from regular group grants.
- **Datasource grants**: Authorize which datasources a designer may query. A designer can only query explicitly granted datasources.

### 5. Configure Apache Superset

Set up Superset self-service once:

- In `config/app.toml`, configure `[bi.superset]` with `self_service = true`, `database_name = "DRS"`, and the Superset admin username in `admin_username`. Store its password in the `DRS_SUPERSET_ADMIN_PASSWORD` environment variable.
- Run `python -m drscore bi sync --group <CODE>` to create the Superset role `DRS_DESIGN_<CODE>`.
- In Superset (**Settings > List Users**), create accounts for dashboard designers and assign them the roles `Gamma` and `DRS_DESIGN_<CODE>`. Never assign `Alpha` or `sql_lab` to dashboard designers, as those roles bypass dataset restrictions.

## Permissions reference

To grant specific capabilities to a user, configure the corresponding grant:

| To let a user ... | Required grant |
| --- | --- |
| View every report in a group | Group grant on that group |
| Export grid results (XLSX, CSV, TXT) | Group grant with **Can export** ticked |
| Refresh cached report results | Group grant with **Can refresh** ticked |
| Create drafts and test SQL queries | Group grant with **Can design** AND an active Datasource grant |
| Publish reports without approval | Group grant with **Can design** and **Can publish** AND active Datasource grant |
| Design BI dashboards in Superset | Superset account with roles `Gamma` + `DRS_DESIGN_<GROUP>`; when its name differs from the DRS user name, enter it in the user's **Superset account** field |

## Daily administration

Perform these routine administration and maintenance tasks:

- **Approve or reject drafts**
  In the **Report design** menu (`/design`), a badge displays the count of pending proposals submitted by designers who lack publishing rights. Inspect header fields, parameters, columns, and the unified SQL diff. Click **Approve** to make the report live, or enter a mandatory reason and click **Reject**.

- **Report maintenance actions**
  Use the action buttons on reports under `/admin/report/`: **Open report** to visit the live view, **Test run** to execute with default parameters, **Check** to inspect validation warnings, and **Open in Superset** to view the dataset.

- **Audit and access logs**
  Inspect `/admin/accesslog/` to see who opened, ran, or exported reports, along with runtime duration, row counts, and parameter values. Inspect `/admin/auditlog/` to review all configuration changes.

- **Cache management**
  Set **Retention (s)** on each report to control cache duration. A value of `0` queries the database every time. Schedule `python -m drscore cache warm --report CODE` via the task scheduler to pre-load heavy reports before business hours.

## Useful command-line tools

Run these administration commands using Python in your server console:

| Command | Description |
| --- | --- |
| `python -m drscore grant show --user U` | Show visible reports, design rights, and datasource grants for user U. |
| `python -m drscore grant group CODE --role R [--design] [--publish]` | Grant group access to role R with optional design and publish rights. |
| `python -m drscore grant datasource CODE --user U` | Grant query rights on datasource CODE to designer user U. |
| `python -m drscore datasource test CODE` | Test connectivity to datasource CODE and show detailed connection errors. |
| `python -m drscore report validate` | Check all report queries, parameters, and design bindings across the system. |
| `python -m drscore db check` | Check database connection and verify migration status. |
| `python -m drscore user set-password USER` | Set or reset the password for user USER from the console. |

## Troubleshooting

Refer to this table to resolve common administrative issues:

| Problem | Cause | Solution |
| --- | --- | --- |
| Lower-case code refused | Code format rejected | Codes must match `^[A-Z][A-Z0-9_]{1,49}$`. Use upper-case letters, numbers, and underscores only. |
| Designer sees no "Report design" menu | Missing design grants | Ensure the designer has both an active group grant with **Can design** AND an active datasource grant. |
| "The data source cannot be reached" | Database connection issue | Run `python -m drscore datasource test CODE` from the terminal to see the exact driver error. |
| User gets 403 on every page | Password change required | The user has "Must change password" ticked. They must visit `/account` to set a new password. |
| Superset user sees no dataset | Inactive or missing role | Ensure the user is Active, the report was run once, and the user has role `DRS_DESIGN_<GROUP>`. |
| Dashboard tab missing on report | No dashboard attached | A dashboard has not been linked to the report yet. Attach one via the BI page (`/design/reports/{code}/bi`). |
