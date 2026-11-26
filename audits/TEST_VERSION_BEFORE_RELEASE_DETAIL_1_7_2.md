# 1.7.2 - work list

**Opened 2026-11-09, at the operator's word** (ORD 437). The procedure is
[`TEST_VERSION_BEFORE_RELEASE.md`](./TEST_VERSION_BEFORE_RELEASE.md). Built from the private repository (on `8476967`).

**Why this version exists:** a public link could only be made with the command line. The operator: *"fix code để có thể
làm trên giao diện, phần mềm drscore là chủ yếu thao tác giao diện"*; *"nên tạo 1 user mặc định sẵn đi, ví dụ
user_public ... rồi gán các public link, report cần public cho user trên là đc"*.

---

## 0. Where 1.7.2 stands

| Item | State |
| --- | --- |
| 1.1 | System -> Public links (`/system/links`, administrators): make, list, switch off / on, public <-> sign-in - **done** |
| 1.2 | Default link account `user_public` (`[public] default_user`), made at start-up, granted the report when a link is made for it - **done** |
| 1.3 | Activity log names `public.run`, `public.export`, `publiclink.add`, `publiclink.change` - **done** |
| Tests | `test_publiclink`, `test_pages`, `test_startup`, `test_settings`, `test_cli`, `test_integrations`, `test_changelog_system`: passed (§2) |
| Worker | 1.7.2 since 2026-11-09T08:34Z; links 8-14 moved to `user_public` (§3) - **done** |
| Commit / publish | not yet: wait for the operator's word |

No migration, no new dependency.

## 1. Items

- `routes/system.py`: `/system/links` GET (list + form: report, then run-as - default `user_public` -, buttons, who,
  view, title, expiry, reload, per parameter "reader may change" + fixed / starting value, choice list), POST create
  (the same `publiclink.create` as the CLI; for the default account, `admin.grant` of the report with the ticked
  export / refresh first, in the same transaction), POST `/system/links/<id>/off|on|public|signin`
  (`revoke` / `enable` / `change`). The address is shown once, at every portal address.
- `admin.service.ensure_public_user` + `startup._public_user`: the account, no password, not an administrator.
- `templates/system_links.html`, menu entry (System), i18n `links.*` (VI / EN), docs `docs/public-links.md`,
  `config/app.example.toml` (+ packaged copy), changelog 1.7.2.

## 2. Tests

| Run | When | Result |
| --- | --- | --- |
| 3 new in `test_publiclink.py` (default account made once; an admin makes a link on the page, the account gets the report with export, off / on / sign-in / public; a link for an account without the report refused) | ~08:20Z | passed |
| `test_publiclink`, `test_pages`, `test_startup`, `test_settings`, `test_cli`, `test_integrations` | ~08:25Z | 77 passed, 1 skipped |
| `test_changelog_system` (the `/changes` check follows the changelog instead of fixed versions) | ~08:30Z | 14 passed |

Full suite not run for this version (1.7.1's full run on IT-DB01: 899 passed, the 2 known PG failures + 3 fixed).

## 3. Build and deploy

| Step | When (UTC) | Result |
| --- | --- | --- |
| Wheel from tree `c44e422a` | 08:33Z | sha256 `eb3ffab459b7c27aa1d9d995afb6c521ab655fe8fcb9c3a8f2e6684db520894a` |
| `drscore-msodbc:1.7.2` = swap on 1.7.1 | 08:34Z | `bcd292571bac` |
| `pg_dump -Fc` before | 08:34Z | `runtime/pre_upgrade_1.7.2/drs.dump`, 229 373 426 bytes |
| `.env` -> `.env.bak-1.7.1`, `docker compose up -d drscore` | 08:34Z | running 1.7.2; start-up: `user "user_public" was made` |
| `user_public` granted the 7 HRMS reports (export, refresh); links 8-14 `publiclink set --as user_public` | 08:35Z | same addresses |
| Headless Edge, the 7 links without signing in | 08:35Z | all as in 1.7.1 (form, buttons, filters, pages), XLSX downloaded |

`link_viewer` is no longer used by any link (left as it is). **Rollback:** `.env.bak-1.7.1`, `docker compose up -d drscore`.

## 4. Publish

Not published.
