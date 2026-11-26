# 1.10.0 - work list

**Opened 2026-11-26, at the operator's word.** The procedure is
[`TEST_VERSION_BEFORE_RELEASE.md`](./TEST_VERSION_BEFORE_RELEASE.md). Built from the private repository, commit `817a149`.

**Why this version exists:** the operator: *"thiết kế lại drscore và drs-superset, dùng kit của dbabrain.dev (nếu kit
chưa đủ thì có thể upgrade kit rồi dùng) ... thiết kế lại web site cho đẹp, gôm nhóm menu hợp lý"*; then *"ok, deploy"*.

---

## 0. Where 1.10.0 stands

| Item | State |
| --- | --- |
| 1.1 | Site kit 1.7.0 (dbabrain.dev, its `kit/` raised from 1.6.0 for this: tiles for any icon font, `.sk-tile`) replaces Shell Kit 1.0 and the vendored Tabler - **done** |
| 1.2 | One menu model (`web/navigation.py`) and one frame (`templates/_shell.html`) for the portal and the admin pages; menu grouped (Administration, Monitoring) - **done** |
| 1.3 | Superset theme from the kit's tokens, logo, favicon, Manrope served by Superset - **done** |
| Tests | full suite on the build PC (§2); headless Edge on a scratch instance (§2) |
| Worker | 1.10.0 since 2026-11-26T07:34Z, Superset rebuilt 07:35Z (§3) - **done** |
| Commit / publish | private `817a149` + docs `1497fa2` (pushed); public: one squashed commit `0ceb267` "Release 1.10.0 (with 1.5.0 - 1.9.0)" from a sanitized export (internal addresses, users and company names replaced in the audit sheets), tag `v1.10.0`, GitHub Actions run 45781598729 success, PyPI `drscore 1.10.0` (wheel + sdist) - **released 2026-11-26** |

No migration, no new Python dependency.

## 1. Items

- `web/static/kit/`: kit.css, kit.js, fonts/ (OFL-1.1), bs/ (kit-bs.css, kit-bs.js, Tabler 1.6.1 css, Bootstrap 5.3.8
  bundle); without Bootstrap Icons, family/ and icons/. `vendor/tabler/` and the Shell Kit files removed.
- `web/navigation.py`, `templates/_shell.html`, `templating.shell_context()`; `base.html`, `login.html`, `public.html`
  (`data-theme-lock` keeps the configured theme), `public_report.html`, `home.html` (group tiles).
- `admin/templates/sqladmin/base.html`, `layout.html`, `index.html` (overrides, BSD-3-Clause beside them);
  `admin/web.py` builds the frame in `authenticate` and shares the portal's templates; `static/css/drs-admin.css`.
- `docker/superset/`: `superset_config.py` (APP_NAME, APP_ICON, FAVICONS, THEME_DEFAULT / THEME_DARK), `Dockerfile`
  (copies `drs-theme/` into Superset's static and `head_custom_extra.html`).
- i18n `nav.section.*`, `nav.adm.*`, `nav.desc.*` (VI / EN); THIRD_PARTY_LICENSES.md (the kit); changelog.

## 2. Tests

| Run | When | Result |
| --- | --- | --- |
| Full suite, SQLite, build PC | ~07:20Z | 517 passed, 6 skipped, 3 failed = `test_default_admin.py` x2 (fail on the unchanged tree too) and the known Windows symlink test |
| Headless Edge on a scratch SQLite instance (demo data): login, home, a report, admin home, admin list and edit, system log, dark mode, phone width and its drawer | ~07:15Z | as expected, no page error |

## 3. Build and deploy

| Step | When (UTC) | Result |
| --- | --- | --- |
| Wheel from `817a149` (LF export) | ~07:29Z | sha256 `ad89467d71cc96abc7f84439230d71b19f0e3c5065ce3d1a2b9cd2fafeea9730` |
| Superset files on the worker equal to the previous commit (md5) before replacing; `.bak-1.9.0` copies kept | 07:31Z | equal |
| `drscore-msodbc:1.10.0` = swap on 1.9.0 | 07:34Z | `bcdc110d16dd` |
| `pg_dump -Fc` before | 07:34Z | `runtime/pre_upgrade_1.10.0/drs.dump`, 245 724 365 bytes |
| `.env` -> `.env.bak-1.9.0`, `docker compose up -d drscore` | 07:34Z | running 1.10.0, healthz ok, no error in the log |
| Superset: old image tagged `drscore-superset:6.1.0-pre-1.10.0` (`f3abeed3a65b`), `compose build superset`, `up -d superset` | 07:35Z | `b78608fb1487`, healthy; logo and fonts.css 200; the login page links them |
| The live sign-in page at the domain | ~07:40Z | the kit's stylesheets load, no page error |

Rollback: `DRSCORE_VERSION=1.9.0` in `.env` + `compose up -d drscore`; Superset: tag `6.1.0-pre-1.10.0` back as
`6.1.0`, restore `superset/*.bak-1.9.0`, `compose up -d superset`.
