# 1.7.1 - work list

**Opened 2026-11-09, at the operator's word** (ORD 437). The procedure is
[`TEST_VERSION_BEFORE_RELEASE.md`](./TEST_VERSION_BEFORE_RELEASE.md). Built on top of the uncommitted 1.7.0 working tree
of the private repository.

**Why this version exists:** the operator opened the seven HRMS report links (public, no sign-in) and found a bare
table: no export button, a company chooser that was a number box, at most 5 000 rows. The operator's words: show it
*exactly like the report page, without the left menu; do not build anything new, call what exists; every table link
the same.*

---

## 0. Where 1.7.1 stands

| Item | State |
| --- | --- |
| 1.1 | A table link (`--view grid`) renders the portal's own report page body (`_report_body.html`, shared with `report.html`) and `report.js`, without the menu - **done** |
| 1.2 | Its calls go to the link: `POST /p/<token>/api/run`, `GET /p/<token>/api/params/<name>/options`, `GET /p/<token>/export` - **done** |
| 1.3 | Export and refresh follow the grant of the link's user; the form shows only the editable parameters - **done** |
| Tests | full suite SQLite + PostgreSQL on IT-DB01: 899 passed, 5 failed = 2 known PG-only + 3 fixed after (§2) |
| Build / worker / check | wheel from tree `c277004d`; on the worker since 2026-11-09T07:36Z; the 7 HRMS links checked in headless Edge (§3) - **done** |
| Commit / publish | not yet: wait for the operator's word |

No migration, no new dependency (wheel swapped into the 1.7.0 image).

---

## 1. Items

### 1.1 The report page on a table link

- `drscore/web/templates/_report_body.html`: the body of `report.html` (form, tabs, buttons, grid / design / dashboard
  panes), its options call written `{{ api_base }}/params/...`. `report.html` includes it with
  `api_base = /api/reports/<code>`; `public_report.html` with `/p/<token>/api`, under a slim title bar (group, code,
  title, the link's refresh) and no menu, account or language buttons.
- `report.js`: `page.api_base` (default `/api/reports/<code>`) for run / grid / bi-token / bi-members; with
  `page.export_base` the export links carry the run's parameters instead of a snapshot id.
- `routes/public.py`: `_link_definition` (the run-as user's definition, grid view only, the editable parameters only,
  each with the link's value), `public_api_run` (the reader's values only for editable parameters - `params_for`;
  same-origin; CSRF-free like `/list`, `deps.CSRF_EXEMPT_PATHS`), `public_api_options`, `public_export` (the same
  run, from the cache while valid, every row, `views.export` with the link user's right). The page logs `OPEN`, a run
  `VIEW` under `public:<token prefix>`.
- The html / bi links keep their own page (`public.html`).
- docs: `docs/public-links.md`; changelog 1.7.1 (EN / VI).

---

## 2. Tests

| Run | When | Result |
| --- | --- | --- |
| `tests/test_publiclink.py` (4 new, 4 adapted to the page asking `/api/run`), `test_report_views`, `test_portal_reports` | 2026-11-09 ~07:20Z | 45 passed |
| Full suite, SQLite + PostgreSQL, on this PC | ~07:25Z | stopped by Claude Code: the build PC ran low on memory - not counted |
| Full suite, SQLite + PostgreSQL 16, on IT-DB01 (container `drs-test-run-171`, Linux) | 07:37-08:13Z | 899 passed, 3 skipped, 5 failed |
| The 3 new failures fixed, `test_changelog_system` + `test_pages` | ~08:20Z | 29 passed |

The 5 failures: the 2 PostgreSQL-only ones known since 1.4.2 (`test_bi_members::test_the_bi_page_saves_the_form`,
`test_selfservice::test_sync_report_on_sqlite_returns_not_available`); `test_pages::test_ui_text_is_not_hard_coded`
(the comment of `_report_body.html` held `{{ api_base }}`, read as page text - comment rewritten, no change to the page);
`test_changelog_system::test_version_shows_and_whats_new_is_per_reader` on both engines (it checked the 1.7.0 lines on
the newest release - now checks them on 1.7.0 itself). The worker's 1.7.1 is not rebuilt: only a template comment differs.

---

## 3. Build and deploy

| Step | When (UTC) | Result |
| --- | --- | --- |
| Wheel from tree `c277004d` | 07:35Z | 3 119 739 bytes, sha256 `95f30306b344caad1a4cec1caf3660d1c7c80764e656e8d0c8b712b43207eb0d` |
| `drscore-msodbc:1.7.1` = `Dockerfile.swap` on `drscore-msodbc:1.7.0` | 07:36Z | `ce9ff5d328e8` |
| `pg_dump -Fc` before the upgrade | 07:36Z | `runtime/pre_upgrade_1.7.1/drs.dump`, 227 684 705 bytes |
| `.env` -> `.env.bak-1.7.0`; `DRSCORE_VERSION=1.7.1`; `docker compose up -d drscore` | 07:36Z | `/healthz` ok, running 1.7.1 |
| `link_viewer`: export + refresh on the 7 HRMS reports (operator) | 07:37Z | done |

**Check**, headless Edge, no sign-in, the 7 HRMS links at the domain: each shows the form (Company a select,
"2 - Company 2"), Refresh / Print / XLSX / CSV / TXT, a filter per column, pages, no menu, no
console error; rows 7 508 / 7 941 / 12 038 / 8 128 / 13 / 184 / 7 865; XLSX of Active & Inactive downloaded
(1.58 MB, 8 128 rows).

**Rollback:** `.env.bak-1.7.0`, `docker compose up -d drscore`.

## 4. Publish

Not published.
