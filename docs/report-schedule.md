# Report schedules: read a report again at set times

Without a schedule DRS reads a report's source only when someone opens the report after its snapshot has
expired (its **retention**, `cache_ttl_seconds`), or presses **Refresh** - at whatever hour that is. For a
report that is heavy on a production server, that may be nine in the morning.

A **schedule** (DRS 1.12.0) makes the report read its source by itself, with its default parameters, inside a
**time window**, and keeps the result for the report's retention. With `--live-runs window` the readers are
kept to that window too: outside it they see the newest data kept, and nothing they do reads the source.

## The time window

The fields, their names and their meaning are those of dbabrain's `time_window`, so a value means the same in
both tools. Every field is optional; the clock is the portal's (`[app] timezone`).

| Field | Meaning |
| --- | --- |
| `from_hour`, `to_hour` | Hours 0-23, **inclusive**: `1` to `4` is 01:00-04:59. `from` above `to` wraps midnight (`22` to `4`) |
| `from_minute`, `to_minute` | Minutes 0-59, inclusive; narrows the hours |
| `from_day`, `to_day`, `from_month`, `to_month`, `from_year`, `to_year` | Day of the month, month, year; inclusive, wrapping |
| `weekdays` | ISO weekdays: 1 Monday .. 7 Sunday, e.g. `[1, 2, 3, 4, 5]`; absent = every day, `[]` = never |
| `repeat_interval` | **Required.** Seconds between run **starts**. `0` = once, `-1` = manual (only a forced run) |
| `retry_interval` | Seconds after a failed run before the next try (default: `repeat_interval`) |
| `timeout` | Seconds a scheduled run may take (default: the report's; at most `[limits] hard_timeout_seconds`) |

**Once a night between 01:00 and 05:00** is `from_hour 1, to_hour 4, repeat_interval 72000`. The interval is
well under a day on purpose: with `86400`, a run that started at 01:00:40 is due at 01:00:40 the next night,
each slow poll pushes it a little later, and one night it falls out of the window. With 72000 (20 hours) the run
is due again from 21:00, and the window opens it at 01:00 every night.

**Retention** is separate: it is how long a snapshot is served before it is read again. With
`--live-runs window`, `86400` is enough (outside the window the newest snapshot is served even after it has
expired). With `--live-runs always`, give more than a day plus the window (`104400`, 29 h), or the snapshot
expires in the evening and the next reader reads the source.

## Live runs

| `--live-runs` | A reader whose snapshot has expired, or who presses Refresh |
| --- | --- |
| `always` (default) | Reads the source at once, as without a schedule |
| `window` | Inside the window: as `always`. Outside: gets the newest snapshot kept for those parameters, with "This report reads its data only at set times (01:00-04:59 every 72000 s). Showing the data read at ..."; the Refresh button is not offered. No snapshot at all for those parameters: "OUTSIDE_RUN_WINDOW" |

A schedule runs the report with its **default parameters**. Readers who choose other values get their own
snapshot, read inside the window only (with `window`). A report whose required parameter has no default cannot
be scheduled: its run fails with `PARAM_INVALID`.

## Setting it up (administrator)

1. Turn the scheduler on in `app.toml` and restart `serve`:

   ```toml
   [schedule]
   enabled = true
   poll_seconds = 60        # how often the server looks for a due schedule
   lock_minutes = 60        # a run's claim; longer than the slowest scheduled report
   ```

2. Give the reports a schedule (several codes at once is fine):

   ```bash
   python -m drscore report schedule set HRS_SCAN_INVENTORY HRS_SCAN_TABLE_SIZE \
          --from-hour 1 --to-hour 4 --every 72000 --timeout 900 --live-runs window
   # or the window as dbabrain writes it:
   python -m drscore report schedule set HRS_SCAN_ORPHAN \
          --window '{"from_hour": 1, "to_hour": 4, "repeat_interval": 72000, "timeout": 900}' --live-runs window
   ```

   A field that is wrong is refused, never dropped: `--weekdays 0` (cron's Sunday) says Sunday is 7 here.

3. Check them, and run one now if you need the data before the night:

   ```bash
   python -m drscore report schedule list            # window, open / closed now, due, last run
   python -m drscore report schedule run HRS_SCAN_INVENTORY   # whatever the window says
   python -m drscore report schedule run --due       # the ones that are due now
   python -m drscore report schedule off HRS_SCAN_ORPHAN      # and `on` again
   ```

The schedules and their last run are also on **Monitoring > Logs > Report schedules** (`/admin/report-schedule/list`).
Each run is in the access log as user `scheduler` (or the CLI's user), view `schedule`, with its rows and time;
a failure keeps its message on the schedule (`last_error`).

## How it runs

- `serve` looks every `poll_seconds` in one background thread per server process, and runs the due schedules one
  after the other. A schedule is claimed in the DRS database first (`lock_minutes`), so two processes, or two
  servers on one database, never run the same report at once.
- A scheduled run always reads the source (even when the snapshot is still valid), moves the older snapshots of
  those parameters to the history, keeps the new one for the retention and loads it into the report's BI dataset.
- A dashboard runs its member reports when it is opened; give each member the same schedule so that every chart
  has the night's data.
- `python -m drscore cache warm` (an administrator's run) reads the source of an expired report whatever its window.
