# From several SQL files to one dashboard - a hands-on manual

This manual takes a set of SQL files, one per data view, and turns them into a finished dashboard in DRS:
one report per file, the charts in Apache Superset, one dashboard that reads all of them, attached to a report,
and links to open it without signing in. Everything here is done by a report designer through the web pages; only
the links (step 8) need an administrator.

It was written from a real build: a timekeeping dashboard made of eight SQL files (headcount by time of day,
daily KPIs, by department, clock-in / clock-out distribution, leave by type, top employees, exceptions by day,
engine health), 8 reports, 17 charts, two dashboard tabs. The names below come from that build; use your own.

Related: [user-guide-designer.md](user-guide-designer.md) (one report and its dashboard, with screenshots),
[public-links.md](public-links.md) (links in detail), [superset-setup.md](superset-setup.md) (Superset for the admin).

## 0. Before you start

| You need | Ask the administrator for |
| --- | --- |
| A DRS account with design rights on a report group, and **Can publish** on it | `grant group` with design + publish |
| The datasource the SQL reads, granted to you | `grant datasource` |
| A Superset account linked to your DRS account, with your group's design role (`DRS_DESIGN_<GROUP>`) | `user set-superset` |
| Superset reachable at an address of the same kind as the portal: **an https portal needs an https Superset** | `[bi.superset] site_urls` (see step 9) |

Plan the dashboard on paper first: for each SQL file, its parameters and their defaults, the chart it feeds, the
colours, and whether it may be shown without signing in (a file that lists people by name usually may not).

## 1. Prepare the SQL files

DRS runs each file as it is, as one batch, with the datasource's read-only login. A few rules keep the build
smooth:

- **One file = one report = one result set.** The last `SELECT` is the report's rows. `#temp` tables and `DECLARE`
  are fine (SQL Server); create no real object.
- **Parameters are named binds `:name`.** Every bind must have a parameter of the same name (step 2).
- **No `:name` inside comments.** DRS binds there too: write `company_id` in a comment, not `:company_id`.
- **Read binds as text and give each a default in the SQL**, so an empty value works:
  ```sql
  DECLARE @CompanyRaw NVARCHAR(20) = :company_id;
  DECLARE @CompanyId  INT = COALESCE(TRY_CONVERT(INT, NULLIF(LTRIM(RTRIM(@CompanyRaw)), '')), 2);
  DECLARE @DateRaw    NVARCHAR(30) = :report_date;
  DECLARE @ReportDate DATE = COALESCE(TRY_CONVERT(DATE, NULLIF(LTRIM(RTRIM(@DateRaw)), '')),
                                      DATEADD(DAY, -1, CAST(GETDATE() AS DATE)));
  ```
- **Name the columns as the charts will show them** (`WorkHeadcount`, `OvertimeHours`, `AttendanceRatePct`): the
  dataset in Superset keeps these names.
- **Long format for series that vary** (one row per date x code): a chart then makes one series per code by itself.
- Test each file on the source first and note its run time; a dashboard runs several of them at once.

## 2. Create one report per SQL file

On **Report design** (`/design`), **New report**, for each file:

1. **Report code** in capitals (`EV2_DAILY_KPI`), **Report name** as the menu shows it, **Retention** in seconds
   (3600 for data that changes once a day), **Group**, **Data source**, a one-line **Description**.
2. Paste the file into **SQL query** unchanged.
3. **Add parameter** for each bind:

   | Kind of value | Data type | Input | Default | Advanced (JSON) |
   | --- | --- | --- | --- | --- |
   | A company, a site | `int` | `select`, options query `SELECT id, label FROM ...` and its datasource | `2` | |
   | One day | `date` | `input` | `@yesterday` | |
   | A range | `date` x 2 | `input` | `@yesterday-29d` and `@yesterday` | |
   | A fixed list | `int` / `text` | `select` | `30` | `{"options_json": ["15", "30", "60"]}` |
   | A bounded number | `int` | `input` | `50` | `{"min_value": "1", "max_value": "500"}` |

   Date tokens: `@today`, `@yesterday`, `@week_start`, `@month_start`, `@month_end`, `@year_start`, each with an
   offset such as `-7d`, `-1m`. Give every parameter a default: the dashboard runs the other reports with theirs.
4. **Create draft**.
5. Under **Test parameters**, type sample values and press **Save & test**. The result shows the first rows and the
   run time. An error names the cause: an options query is tested too (a wrong column there fails the test).
6. **Fill columns from test**, adjust labels and formats if you like, then **Save & test** again.
7. **Publish**. DRS creates the report's BI dataset in Superset in the background.

Do the same for every file, then **open each report once and press Run report**: the dataset needs a run to hold
data, and the BI page (step 3) shows *Dataset ready in Superset* when it does.

## 3. Open the datasets in Superset

On a report, **Design > BI settings** (`/design/reports/<code>/bi`): the dataset table, its status and **Open in
Superset**. Sign in to Superset with your Superset account. Every report of yours is a dataset
`drs_bi.<report code in lower case>`.

The dataset holds **every cached run** of the report (column `drs_snapshot_id`). While you build, the preview adds
them up; in DRS each reader sees only the run on screen. **Never save a filter on `drs_snapshot_id`.**

## 4. Build the charts

For each chart: Superset **Charts > + Chart** (or Explore from the BI page), pick the dataset and the chart type,
fill the Data panel, **Create chart**, then **Save**: type the chart's name and, in **Add to dashboard**, the
dashboard's name. The first chart creates the dashboard ("A new dashboard will be created"); pick it from the list
for the others.

What worked for each kind of view:

| View | Superset chart | Settings |
| --- | --- | --- |
| How many in each state through the day | **Area Chart** | X-axis = the time label; one metric per state (`SUM(WorkHeadcount)` "Work", ...); Stacked Style **Stack** |
| KPI tiles | **Big Number with Trendline** | X-axis = the date, time grain Day, one metric; it shows the range's last day and its trend |
| Trends over the range | **Line Chart** | X-axis = the date, several metrics |
| Ranking by department | **Bar Chart**, Orientation **horizontal** | X-axis = department; metric; Series limit / Row limit 20; sort by the metric |
| Two directions (in above, out below) | **Bar Chart** | the second metric as Custom SQL `-SUM("ClockOutCount")` |
| Share by type | **Pie Chart**, Donut | Dimension = type; metric; Row limit 8 |
| A table beside a chart | **Table**, Raw records | the columns; **Show cell bars** |
| Codes per day | **Bar Chart**, Stack | X-axis = the date; Dimensions = the code (long format) |
| Runs and their time | **Bar Chart** (stacked counts) + **Line Chart** (P50 / P95) | two charts side by side |

**A stacked area layer that is 0 still draws its outline** along the top of the stack: a layer on top (a break, for
example) shows as a coloured border where it has no value. Write its metric as Custom SQL
`NULLIF(SUM("BreakHeadcount"), 0)`: Superset leaves empty points undrawn, and the border disappears where there is
nothing.

**A "vs day before" figure on a KPI tile compares with the previous row**, whatever that day was: after a holiday or
a Sunday it shows nonsense (+21 000 %). Leave the comparison empty when the series has such days.

## 5. Arrange the dashboard

Open **Dashboards > <your dashboard> > Edit dashboard**:

- **Layout**: drag the charts into rows (12 columns wide): a row of KPI tiles (width 2 each), the main chart full
  width, then pairs side by side.
- **Tabs**: drag a **Tabs** element from *Layout elements* to put a separate subject (here *Engine*) on its own tab.
- **Colours**: **... > Edit properties > Advanced**, `label_colors` in the JSON metadata, one colour per series name,
  so a series keeps its colour in every chart:
  ```json
  "label_colors": {"Work": "#22c55e", "OT": "#86efac", "Break": "#fdba74", "Failed": "#ef4444"}
  ```
- **Save**, then click the **Draft** badge so that it reads **Published**.

## 6. Attach the dashboard to a report

A dashboard is shown in DRS on one report, its **host**; the charts may read the datasets of the other reports
(its *members*).

1. Choose the host: the report whose parameters the reader should change first (here the daily KPIs: company and
   date range).
2. On the host's BI page, the dashboard is listed as **Usable**, "also reads <the other reports>". Press **Use for
   this report**.
3. Open the host report: a **Dashboard** tab. When a reader runs it, DRS runs the member reports too, each with its
   defaults, and every chart shows the reader's own runs.

The dashboard may not read reports with row filters, and every report it reads must be one the reader may open.

## 7. Check as a reader

Open the host report, run it, open **Dashboard**: every chart filled, the tabs, the colours. Change a parameter and
run again: the charts follow. Then sign in as a plain viewer (not a designer) and check the same.

## 8. Links without signing in (administrator)

Designers do not make links; the administrator does, on **System > Public links** or with the CLI. A link runs as a
viewer account, which must be granted the host **and every member** report (view only is enough):

```bash
for c in EV2_DAILY_KPI EV2_HEADCOUNT_BY_SLOT EV2_BY_ORG_UNIT EV2_PUNCH_DISTRIBUTION EV2_LEAVE_BY_TYPE \
         EV2_FLAGS_BY_DAY EV2_ENGINE_HEALTH; do
  python -m drscore grant report $c --user link_viewer --no-export --no-refresh
done
python -m drscore publiclink add EV2_DAILY_KPI --as link_viewer --view bi \
       --editable company_id,from_date,to_date --title "Timekeeping dashboard"
```

A report that names people gets its own link that asks for a sign-in and runs with the reader's own rights; do not
grant it to the link's viewer account:

```bash
python -m drscore publiclink add EV2_TOP_EMPLOYEES --as report_designer --public no --view grid \
       --editable company_id,from_date,to_date,rank_by,top_n
```

The address is shown once; keep it where your team keeps such things.

## 9. If the dashboard does not show

| What you see | Why, and the fix |
| --- | --- |
| BI page: dataset not ready | The report has not run yet: run it once, or **Sync now** |
| Dashboard not listed as Usable | A chart reads a dataset that is not a DRS report's, or a member report has row filters |
| "The BI system cannot be reached right now" on an **https** portal | The page is https and Superset http: the browser blocks the frame (mixed content). Give Superset its own https address (a reverse proxy) and set it for that portal address in `[bi.superset] site_urls`; add the portal's address to Superset's allowed origins (`DRS_PORTAL_ORIGIN`) |
| Charts add up several runs in Superset | Normal while building; DRS shows one run to each reader |
| A series has the wrong colour | `label_colors` names must match the metric labels exactly |

## Checklist

- [ ] Each SQL file tested on the source; binds read as text with defaults; no `:name` in comments
- [ ] One report per file, every parameter with a default, tested, columns filled, published, run once
- [ ] Charts saved into one dashboard; empty stacked layers as `NULLIF(..., 0)`; no `drs_snapshot_id` filter saved
- [ ] Layout, tabs, `label_colors`; dashboard **Published**
- [ ] Attached to the host report; checked as a designer and as a viewer
- [ ] Link viewer granted the host and every member; reports with names behind a sign-in link
- [ ] Portal and Superset on the same scheme (https with https)
