# Mirrors: copies of source tables in the DRS database

A report that aggregates millions of rows of a production database - an ERP - loads that server
every time it runs, and a dashboard that many people open runs it often. A **mirror** copies the
source tables a report needs, **one table at a time**, into the DRS database, on a schedule. The
report is then written against the copy, through an ordinary datasource: the heavy joins and
aggregations run on the DRS PostgreSQL, and the source only answers plain reads of one table,
off its busy hours if you want. One mirrored table serves every report that reads it.

| | |
| --- | --- |
| Where the copies are | PostgreSQL: schema `[postgresql] mirror_schema` (default `drs_mirror`) of the DRS database. SQLite: the file `[sqlite] mirror_path` (default `runtime/mirror/mirror.sqlite3`). |
| What a copy holds | the source columns (lower case), plus `_source` - the code of the datasource the row came from - and `_synced_at` (UTC). |
| Who runs them | `serve`, in the background, when `[mirror] enabled = true`; or `python -m drscore mirror run`. |
| Who defines them | an administrator, with the CLI (`mirror add`, `mirror import`). |

## Two modes

* **full** - every row that passes the mirror's filter is read again and replaces the mirror's rows.
  Right for small tables and for *states* (open order lines, on-hand stock, open customer
  transactions): a row that leaves the filter must leave the copy.
* **incremental** - only the rows whose *watermark* column is above the last value read: a
  modification time (`MODIFIEDDATETIME` in Dynamics 365 / AX) or a growing id. They are inserted or
  updated by the row key. A time watermark should have a look-back (`--lookback 3600`): a transaction
  that committed late with an earlier time is read again, never lost. Rows deleted at the source,
  or that left the filter, stay in the copy until a periodic full copy (`--full-every 168` hours);
  `--full-hours 1-5` keeps that full copy at night while the incremental runs go on all day.

Each run reads the source in one read-only transaction (rolled back at the end) and writes the copy
in one transaction: a report reading the copy sees the old rows until the new ones are all there.
On SQL Server, `--read-uncommitted` reads without shared locks (for a database without read-committed
snapshot); a row read twice is written once. A date watermark (no time of day) reads its last day
again. On PostgreSQL the table's planner statistics are refreshed after a full copy. On SQLite a copy
holds the mirror file's write lock while it runs: a report reading the file waits for it.

## Several servers, one table

Two servers with the same database (two ERP instances, a factory database per site) write one
target table - give each mirror the same `--target` and the same `--key`. Every row carries its
`_source`, and a full copy replaces only the rows of its own source. A report then reads every
company of every server with one query:

```sql
SELECT lower(dataareaid) AS company, date_trunc('month', invoicedate) AS month, sum(lineamountmst) AS revenue
FROM custinvoicetrans
WHERE invoicedate >= :from_date
GROUP BY 1, 2
```

## Commands

```bash
# A big table: invoice lines since 2025, every hour what changed, a full copy every week at night.
python -m drscore mirror add ERP_A.CUSTINVOICETRANS --datasource ERP_A --source dbo.CUSTINVOICETRANS \
    --target custinvoicetrans --columns RECID,DATAAREAID,INVOICEID,INVOICEDATE,ITEMID,QTY,LINEAMOUNTMST,MODIFIEDDATETIME \
    --where "INVOICEDATE >= '2025-01-01'" --mode incremental --key RECID --watermark MODIFIEDDATETIME \
    --lookback 3600 --interval 60 --full-every 168 --full-hours 1-5

# A state: open order lines, whole, every two hours.
python -m drscore mirror add ERP_A.SALESLINE --datasource ERP_A --source dbo.SALESLINE --target salesline_open \
    --where "SALESSTATUS = 1 AND REMAINSALESPHYSICAL > 0" --key RECID --interval 120

python -m drscore mirror run ERP_A.CUSTINVOICETRANS      # now (the first run is a full copy)
python -m drscore mirror run --due                        # what the server would run now
python -m drscore mirror list                             # every mirror, its last run, rows, seconds
python -m drscore mirror show ERP_A.CUSTINVOICETRANS      # definition and last run, as JSON
python -m drscore mirror set ERP_A.CUSTINVOICETRANS --interval 30 --reset-watermark
python -m drscore mirror export mirrors.json              # all definitions
python -m drscore mirror import mirrors.json              # add or change many at once (all or nothing)
```

An import file is `{"mirrors": [{...}, ...]}`; each entry has the keys of `mirror show` (`code`,
`datasource`, `source`, `target`, `columns`, `where`, `key`, `mode`, `watermark`,
`lookback_seconds`, `interval_minutes`, `full_refresh_hours`, `full_hours`, `run_hours`,
`read_uncommitted`, `active`). `--hours 0-6,22-23` (`run_hours`) limits every run of a mirror to
those hours, in `[app] timezone`.

The target table is created at the first run. A column added to `--columns` later is added to the
table; a column is never dropped or changed (another source, or an older definition, may still use
it). A definition whose source, filter or watermark changes starts again with a full copy.

## Reading the copies from a report

Create a datasource on the mirror, with a login that can read **only** the mirror schema - never the
DRS login, which can read the password hashes and secrets of schema `drs`:

```sql
-- once, as a PostgreSQL superuser
CREATE ROLE drs_mirror_ro LOGIN PASSWORD '...';
GRANT CONNECT ON DATABASE drs TO drs_mirror_ro;
```

```toml
# app.toml: DRS grants it USAGE on the schema and SELECT on every table it creates
[mirror]
enabled = true
read_role = "drs_mirror_ro"
```

```bash
python -m drscore datasource add MIRROR --type postgresql --host <db host> --port 5432 --database drs \
    --user drs_mirror_ro --schema drs_mirror
```

Reports on `MIRROR` are written in PostgreSQL SQL, with unqualified table names (the datasource's
schema comes first in the search path). On SQLite, the datasource is of type `sqlite` on the file
`mirror_path`.

## Settings (`app.toml`, `[mirror]`)

| Key | Default | |
| --- | --- | --- |
| `enabled` | `false` | `serve` runs the mirrors that are due |
| `poll_seconds` | `60` | how often the server looks for a due mirror |
| `batch_rows` | `10000` | rows read and written at a time |
| `timeout_seconds` | `3600` | the longest one source query may take |
| `lock_minutes` | `15` | a run's claim, renewed every minute while it copies: a server stopped mid-copy frees it |
| `read_role` | `""` | PostgreSQL role given SELECT on the mirror tables |

Every server process looks for due mirrors; a mirror is claimed in the DRS database before it runs,
so two processes - or two servers on one database - never copy the same table at once. Changes to
definitions are in the audit log (`MIRROR_ADD`, `MIRROR_SET`); each run is in the application log
and in `mirror list` (status, rows, seconds, error).
