# 1.6.0 - work list

**Opened 2026-10-24, at the operator's word.** The procedure is
[`TEST_VERSION_BEFORE_RELEASE.md`](./TEST_VERSION_BEFORE_RELEASE.md).

**Why this version exists:** an IT expense report counts "the IT equipment every department buys",
and which kinds of equipment that means (laptops yes, TVs?, barcode scanners?) is the IT manager's
decision, not a rule to write into the report's SQL nor a field to set asset by asset in the source
system. The operator, 2026-10-24 (translated): *a page like the dashboard's public link, showing the
types, where the IT manager ticks which types count as IT expense, the decided ones ticked already;
and the report joins this type table.* Ready tools were weighed first: Superset has no write-back;
Metabase's free edition writes only through forms and not on public dashboards (its editable grid is
a paid feature); Appsmith would do it as one more heavy service to run; NocoDB needs an account to
edit. The operator chose to build it into DRS.

---

## 0. Where 1.6.0 stands

| Item | State |
| --- | --- |
| 1.1 | choice lists: kept by DRS, copied into a table of the mirror schema for the reports (migration 0016) - **done** |
| 1.2 | a public link may tick one list: `GET` / `POST /p/<token>/list` - **done** |
| 1.3 | CLI `choicelist import / export / list / show / tick / publish`; `publiclink add / set --list` - **done** |
| Tests | full suite on SQLite and PostgreSQL: 796 passed, 2 failed - the 2 PostgreSQL-only failures of 1.4.2 onwards (§2) |
| Build | wheel from an LF export of the working tree (a git tree, **not a commit**: §3) |
| Worker | `drscore-msodbc:1.6.0` running since 2026-10-24T08:48Z, migrated 0015 -> 0016 - **done** |
| Check | a list ticked and saved anonymously on a public link, the reports' table and the audit row seen (§3) - **done** |
| Commit / publish | not yet: waits for the operator's word (1.5.0 and 1.5.1 are not published either) |

---

## 1. Items

### 1.1 Choice lists

- `drs_choice_list` (code, title, description, target table, active, last change) and
  `drs_choice_item` (key, label, group, detail, sort, ticked, last change); migration 0016, which also
  adds `drs_public_link.choice_list_id`. SQLite rebuilds the link table keeping AUTOINCREMENT; the
  downgrade drops the column and the two tables.
- `choicelist.put` creates or describes a list again from JSON. **An item already in the list keeps
  its tick** - the file's `checked` only ticks a new item - unless `--reset`; an item no longer named
  is removed. Keys `[A-Za-z0-9_.-]`, at most 500 items, a plain lower-case table name; a mirror's
  table or another list's is refused.
- `choicelist.publish` writes the list, whole and in one transaction, into `<mirror schema>.<table>`
  (`item_key` primary key, `label`, `group_name`, `sort_order`, `is_checked`, `changed_at`,
  `changed_by`), made if missing and granted to `[mirror] read_role` as the mirrors are, then
  `ANALYZE`s it: a report joining a new table with no statistics was planned as loops (2 s became 15 s).
- Every change of a tick is audited `CHOICELIST_TICK` (before / after, and the reader's address).

### 1.2 A public link that ticks a list

- `publiclink add|set --list CODE` (view `html` only: the page saves it).
- `GET /p/<token>/list`: the list and its ticks now (not from a snapshot).
- `POST /p/<token>/list` with `{"checked": {key: bool}}`: JSON only (415 / 422 otherwise), an
  `Origin` of this server when the browser sends one (403 otherwise), keys of that list only (400),
  a link unusable for any reason answers 404 like every public address. The session CSRF check
  exempts exactly `/p/<token>/list` (a public reader has no session; the secret address is the right
  to tick).
- docs: `docs/choice-lists.md`; `docs/public-links.md` (`--list`); README.

---

## 2. Tests

| Run | When | Result |
| --- | --- | --- |
| `tests/test_choicelist.py` (16), SQLite and PostgreSQL 16 | 2026-10-24 | 32 passed |
| Full suite on the working tree, SQLite and PostgreSQL 16 | 2026-10-24, before 08:30Z | 796 passed, 3 skipped, 2 failed |
| `test_choicelist.py` + `test_publiclink.py` after the `ANALYZE` change | 2026-10-24, 08:46Z | 60 passed |

The new tests: a list copied for the reports and its ticks kept by a new import (and set by
`--reset`); a tick checked and audited; what a list must be (7 cases); no two lists on one table;
the CLI; a link reading and saving its list; refused saves (another origin, not JSON, an unknown
key, a wrong value, a link without a list, a revoked link); public links off; only an html link and
only an active list; the migration down and up again.

The 2 failures are the PostgreSQL-only tests that fail since 1.4.2
(`test_bi_members::test_the_bi_page_saves_the_form`, `test_selfservice::test_sync_report_on_sqlite_returns_not_available`).
`test_designs::test_a10_symlink_out_of_the_root_is_invalid` deselected (Windows symlink privilege).

---

## 3. Build and deploy

**Not as §3 rule 2 says:** the wheel was built from the working tree, not from a `Release 1.6.0`
commit - a commit waits for the operator's word. The export is reproducible: the working tree was
staged into a temporary index (the real index and branches untouched), written as a git tree and
exported with `git -c core.autocrlf=false archive <tree>` (LF, no tests). No database dump was taken
before the upgrade (1.5.1 took one); one was taken right after it.

| Step | When (UTC) | Result |
| --- | --- | --- |
| Wheel from tree `cffb2a34` (first build) | 08:42Z | sha256 `1570ebb0...a292ae`, kept on the worker as `.whl.1570ebb` |
| `drscore:1.6.0` / `drscore-msodbc:1.6.0` (first) | 08:42-08:43Z | `aeec8c8639ff` / `e75f78de8ce6` |
| `.env` -> `.env.bak-1.5.1`; `DRSCORE_VERSION=1.6.0`; `docker compose up -d drscore` | 08:43:38Z | migrated 0015 -> 0016, `/healthz` ok |
| Rebuilt (not public): `publish` runs `ANALYZE` | 08:47Z | wheel from tree `f748153a`, 3 025 838 bytes, sha256 `8e0eb852fb37c1b54801a460e81a40c88413afaf6765adda419971930445c5f4` |
| `drscore:1.6.0` / `drscore-msodbc:1.6.0`; `docker compose up -d drscore` | 08:47:43-08:48:41Z | `39ac5881d569` / `340f6b144744`, `/healthz` ok |
| `pg_dump -Fc` of the DRS database | 08:50Z | `runtime/post_upgrade_1.6.0/drs.dump`, 147 954 590 bytes |

**Kept for rollback:** the 1.5.1 images and wheel, `.env.bak-1.5.1`, the dump above. Rollback to
1.5.1 needs migration 0016 down first (the column and the two tables; tested on both engines).

**Check on the worker** (public links only - the portal sign-in was not tried this time):
- List `IT_DEVICE_TYPE` (19 kinds, all ticked) imported in the container; its table readable by the
  mirror read role; a public link of the IT type page allowed to tick it (`publiclink set --list`).
- Opened anonymously in headless Edge at 1600 and 412 wide: one kind unticked -> "1 change not saved
  yet", the address `#hash` holding it; Save -> saved; a new page shows it unticked; the reports'
  table and an audit row `CHOICELIST_TICK` (the link's hint, the address) seen in the database; the
  IT expense report's SQL then leaves that kind out; ticked again the same way. No console error, no
  horizontal scroll; the container log shows requests only.

---

## 4. Publish

Not done: waits for the operator's word (commit `Release 1.6.0`, `main` push, tags `v1.5.0`,
`v1.5.1`, `v1.6.0`).
