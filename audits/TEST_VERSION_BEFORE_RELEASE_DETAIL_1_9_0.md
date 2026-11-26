# 1.9.0 - work list

**Opened 2026-11-11, at the operator's word** (ORD 437). The procedure is
[`TEST_VERSION_BEFORE_RELEASE.md`](./TEST_VERSION_BEFORE_RELEASE.md). Built from the private repository (on `ef60212`
+ the uncommitted 1.8.0).

**Why this version exists:** the operator: *"khi xem report dạng grid, chưa thể hide column, hoặc kéo column sang vị trí
khác?"*; *"bật các tính năng của thư viện có hỗ trợ luôn đi, kể cả nhớ cách sắp xếp trong trình duyệt"*; and for the
export following the arranged table: *"làm thêm phần này luôn"*.

---

## 0. Where 1.9.0 stands

| Item | State |
| --- | --- |
| 1.1 | Grid (Tabulator 6.6.1, already vendored): drag a header to move a column (`movableColumns`), hide / show (Columns button with ticks; the column's ⋮ menu), pin to the left (⋮ menu), layout + sort + page size kept per report in the browser (`persistence`), "Default layout" - **done** |
| 1.2 | XLSX / CSV / TXT follow the visible columns in their order on screen (`cols`), portal and public links - **done** |
| Tests | 2 new; full suite on the build PC (§2); browser check (§2) |
| Worker | 1.9.0 since 2026-11-11T02:21Z (§3) - **done** |
| Commit / publish | not yet: wait for the operator's word |

No migration, no new dependency.

## 1. Items

- `static/js/report.js` (portal report page and table links share it): `movableColumns`, `persistence {columns:
  [width, visible, frozen], sort, page size}`, `persistenceID` = report code + a hash of the run's column names (a
  report whose columns change starts from its own layout); `headerMenu` per column (hide, pin / unpin - a pinned
  column moves after the pinned ones, as Tabulator keeps frozen columns at the edge -, Columns); the Columns list
  (one column always stays); Default layout clears the three storage keys and draws the grid again; the export links
  get `&cols=` at click time from the grid on screen.
- `templates/_report_body.html`: the Columns dropdown (Tabler / Bootstrap). i18n `grid.columns*`, `grid.*_column`,
  `grid.pinned` (VI / EN).
- `views.export_columns(columns, cols)`: `cols` = positions among the grid's columns (the visible ones, as a run
  answers them), each once; the design's non-exported columns stay out; exported columns not in the grid come
  after; anything else is PARAM_INVALID. `views.export(..., cols)`; `/api/reports/<code>/export?cols=` (max 4000
  characters) and `/p/<token>/export?cols=` (not taken as a report parameter).
- Changelog 1.9.0 (VI / EN), `docs/public-links.md`, `pyproject.toml`.
- Test fix: `test_publiclink.py::test_cli` passes `--` before the token prefix (a token starting with `-` was read as
  an option: the old random failure).

## 2. Tests

| Run | When | Result |
| --- | --- | --- |
| 2 new: the file follows `cols` (order, hidden, non-exported, exported-but-hidden, bad values); a table link's file follows `cols` and its page has the Columns button | ~02:00Z | passed |
| Full suite, SQLite, build PC (without `test_default_admin.py`, failing on the unchanged tree, see 1.8.0) | ~02:15Z | 507 passed, 6 skipped, 1 failed = the known Windows symlink test |
| Headless Edge on a scratch SQLite instance (demo data, a table link): hide from the list, drag a header, pin from the menu, CSV header = the screen, reload keeps it, Default layout resets it, no page error | ~02:10Z | all as expected (columns pinned by the design cannot be dragged: Tabulator's rule) |

## 3. Build and deploy

| Step | When (UTC) | Result |
| --- | --- | --- |
| Wheel from tree `97a93551` (LF export) | ~02:16Z | sha256 `cec9b9a0fb3713b73a194d33c630c4386363be6f1e47610cfcdb74a049f974c2` |
| `drscore-msodbc:1.9.0` = swap on 1.8.0 | 02:21Z | `8f337adcaf00` |
| `pg_dump -Fc` before | 02:21Z | `runtime/pre_upgrade_1.9.0/drs.dump`, 234 151 843 bytes |
| `.env` -> `.env.bak-1.8.0`, `docker compose up -d drscore` | 02:21Z | running 1.9.0, healthz ok |
| Headless Edge, a live HR table link at the domain (36 columns, ~8k rows): 31 columns unticked, CSV = the 5 shown, 8 128 rows + header; reload keeps 5; Default layout back to 36 | ~02:25Z | as expected |

**Rollback:** `.env.bak-1.8.0`, `docker compose up -d drscore`.

## 4. Publish

Not published.
