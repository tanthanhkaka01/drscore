# Release procedure - qualifying a drscore version before it is published

**Undated on purpose.** This is *what* every version goes through, step by step, and what counts as
passing each step. Every version writes its own sheet beside this file and fills one row of §1.
Updated in place when the procedure changes - never to match a run.

- **Per version:** `TEST_VERSION_BEFORE_RELEASE_DETAIL_X_Y_Z.md` holds what that version changed and
  how its build, tests and deploy went; the current one is
  [`…DETAIL_1_7_0.md`](./TEST_VERSION_BEFORE_RELEASE_DETAIL_1_7_0.md). A sheet may add steps; it may
  never drop one from §5.
- **Where it runs:** the worker - the server where drscore runs beside Apache Superset, from a wheel
  built on the build PC (see the header of `docker-compose.yml` on the worker).
- **Public repository:** sheets name no internal address, host, database, user or secret.

---

## 1. Version ledger

One row per version, **appended, never rewritten after it is public**. All times UTC. Rows before
1.4.2 were backfilled on 2026-10-15 from git, from the wheels kept on the worker and from PyPI; for
them "Deployed" is when the wheel reached the worker.

| Version | Status | Commit | Wheel built | Deployed on the worker | Public release (PyPI) | In one line |
| --- | --- | --- | --- | --- | --- | --- |
| 1.0.0 | `pre-rule` | `a817c50` 2026-10-07T08:06Z | - | - (installed from PyPI then) | 2026-10-07T08:09Z | The package and distribution renamed `drscore`; SQLite by default; release workflow |
| 1.1.0 | `pre-rule` | `dc2a903` 2026-10-08T03:41Z | - | - (installed from PyPI then) | 2026-10-08T03:42Z | No notes in the commit |
| 1.1.1 | `pre-rule` | `799c7e5` 2026-10-08T08:08Z | - | 2026-10-09T06:33Z | 2026-10-08T08:09Z | No notes in the commit; the first wheel kept on the worker |
| 1.2.0 | `pre-rule` | `d38bc74` 2026-10-10T05:19Z | - | 2026-10-10T05:30Z | 2026-10-10T05:19Z | Report designer role: non-admin users write, test and propose reports (migration 0009) |
| 1.3.0 | `pre-rule` | `f912728` 2026-10-10T06:47Z | - | 2026-10-10T06:49Z | 2026-10-10T06:47Z | Self-service reports and dashboards; "Can publish" on group grants (migration 0010) |
| 1.3.1 | `pre-rule` | `888cf49` 2026-10-10T07:22Z | - | 2026-10-10T07:23Z | 2026-10-10T07:22Z | Fix: the BI page listed no Superset dashboard (Superset 6.1 answer shape) |
| 1.4.0 | `pre-rule` | `59a5622` 2026-10-15T01:33Z | - | 2026-10-15T01:34Z | 2026-10-15T01:39Z | A report's dashboard may hold charts on other reports' datasets, each with its own parameters |
| 1.4.1 | `pre-rule` | `4bcba6f` 2026-10-15T03:17Z | - | 2026-10-15T03:17Z | 2026-10-15T03:20Z | A DRS user has a Superset account field (migration 0012) |
| 1.4.2 | `released` | `656f0d9` 2026-10-15T08:22Z | 2026-10-15T08:23Z, sha256 `800228e4…87e6f5` | 2026-10-15T08:24:20Z | 2026-10-15T08:40:18Z (tag `v1.4.2` pushed 08:39:33Z) | Solid status badges readable again (Tabler 1.6 `text-<colour>-fg`) |
| 1.5.0 | `deployed` | `720af1e` 2026-10-22T07:49Z | 2026-10-22T07:49Z, sha256 `e875ab52…4cec3c` | 2026-10-22T07:51:05Z (first build of `79e300c` at 07:21:17Z, replaced) | - | Mirrors: source tables copied into the DRS database on a schedule (migration 0013); public links without login (migration 0014) |
| 1.5.1 | `deployed` | `0edb7a5` 2026-10-22T09:44Z | 2026-10-22T09:44Z, sha256 `e4e1be1c…92fa2b7` | 2026-10-22T09:45Z (images `b685d9be0e30` / `2df422b40c80`) | - | Public links show an HTML design (migration 0015), refreshed behind the reader; `publiclink set` keeps a link's address |
| 1.6.0 | `deployed` | - (not committed yet: wheel from working-tree git tree `f748153a`) | 2026-10-24T08:47Z, sha256 `8e0eb852…45c5f4` | 2026-10-24T08:48:41Z (first build `1570ebb` at 08:43:38Z, replaced; images `340f6b144744` / `39ac5881d569`) | - | Choice lists ticked on a public link, joined by reports (migration 0016) |
| 1.6.1 | `deployed` | - (not committed yet: wheel from working-tree git tree `d75b5efc` of the private repository) | 2026-10-26T04:33Z, sha256 `31b7e79f…60130aa7` | 2026-10-26T04:46:20Z (images `aa140aa8bfd3` / `0854c0ac8c70`; dump before the upgrade) | - | Per-user session days (migration 0017): a view-only account stays signed in 30 days, the browser closed or not |
| 1.6.2 | `deployed` | - (not committed yet: wheel from working-tree git tree `ff84dbdc` of the private repository) | 2026-10-26T05:49Z, sha256 `29e76a25…a87e0afe` | 2026-10-26T05:51:05Z (images `9074de1dcf3d` / `d9ffaf1aa270`; dump before the upgrade) | - | A link may be public or not (migration 0018): not public = sign-in and the reader's own rights; `publiclink enable` |
| 1.6.3 | `deployed` | - (not committed yet: wheel from working-tree git tree `2d42d010` of the private repository) | 2026-10-26T06:49Z, sha256 `dc579054…62e6804` | 2026-10-26T06:50:51Z (images `2bc06d730430` / `0f9ececaa745`; dump before the upgrade) | - | A link's editable parameters show the value in use (defaults resolved) in an input of their type |
| 1.6.4 | `deployed` | - (not committed yet: wheel from working-tree git tree `82881bbc` of the private repository) | 2026-11-09T02:45Z, sha256 `468d8d85…59e6aeaf` | 2026-11-09T02:52Z (image `5e517149138c` = 1.6.3 + the wheel swapped in; dump before the upgrade) | - | The portal and Superset opened at several addresses (an IP over http, a domain over https behind a proxy), all set in config |
| 1.7.0 | `deployed` | - (not committed yet: wheel from working-tree git tree `d96e9677` of the private repository) | 2026-11-09T04:45Z, sha256 `123a9744…04bf0fbd` | 2026-11-09T04:49Z (images `bf8ff27ba91b` / `156e3469b4ab`; migration 0019; dump before the upgrade) | - | Activity log of every user action; Góp ý / feedback; the AI agent Typhon; Telegram / Zalo notices; encrypted integration secrets (migration 0019) |
| 1.7.1 | `deployed` | - (not committed yet: wheel from working-tree git tree `c277004d` of the private repository) | 2026-11-09T07:35Z, sha256 `95f30306…3207eb0d` | 2026-11-09T07:36Z (image `ce9ff5d328e8`, 1.7.0 + the wheel; dump before the upgrade) | - | A table link shows the portal's report page without the menu: form, buttons (export all rows, print, refresh), filters, pages |
| 1.7.2 | `deployed` | - (not committed yet: wheel from working-tree git tree `c44e422a`) | 2026-11-09T08:33Z, sha256 `eb3ffab4…db520894a` | 2026-11-09T08:34Z (image `bcd292571bac`, 1.7.1 + the wheel; dump before) | - | System -> Public links: links made on a page; default link account `user_public` |
| 1.8.0 | `deployed` | - (not committed yet: wheel from working-tree git tree `837d3a75`) | 2026-11-11T01:37Z, sha256 `16ba3dd1…ed873700697765c3b86e35355bbd0293cd` | 2026-11-11T01:45Z (image `2f3571f0a5c9`, 1.7.2 + the wheel; migration 0020; dump before) | - | A public link may be shown in other sites' frames (`*` or listed sites), set per link on System -> Public links |
| 1.9.0 | `deployed` | - (not committed yet: wheel from working-tree git tree `97a93551`) | 2026-11-11T02:16Z, sha256 `cec9b9a0…4a049f974c2` | 2026-11-11T02:21Z (image `8f337adcaf00`, 1.8.0 + the wheel; dump before) | - | Grid columns moved / hidden / pinned by the reader, kept by the browser; exports follow the columns shown |
| 1.10.0 | `released` | `817a149` (private repository, not pushed) | 2026-11-26T07:29Z, sha256 `ad89467d…fafeea9730` | 2026-11-26T07:34Z (image `bcdc110d16dd`, 1.9.0 + the wheel; dump before); Superset image rebuilt `b78608fb1487` (old kept as `6.1.0-pre-1.10.0`) | 2026-11-26 (tag `v1.10.0` on the public repository, one squashed commit `0ceb267` with 1.5.0 - 1.9.0; PyPI wheel + sdist) | The portal, its admin pages and Superset on the site kit 1.7.0; one grouped menu for portal and admin |
| 1.11.0 | `released` | `961787f` (private repository, pushed) | 2026-11-26T13:58Z, sha256 `08c313c7…2dcd760d464` | 2026-11-26T14:01Z (image `f0b29a9f1cdb`, 1.10.0 + the wheel; dump before) | 2026-11-26T14:05Z (tag `v1.11.0`, public commit `74dd9bb`; PyPI wheel + sdist) | Mirror copies on PostgreSQL are UNLOGGED tables; an empty incremental copy is filled in full |
| 1.12.0 | `released` | `cfd4ebb` (private repository, pushed) | 2026-11-27T03:27Z, sha256 `f6372548…f4f8ad29a3568e` | 2026-11-27T03:30Z (image `c9911055368`, 1.11.0 + the wheel; migration 0021; dump before; `[schedule] enabled`) | 2026-11-27T03:59Z (tag `v1.12.0`, public commit `7fe7456`; PyPI wheel + sdist) | Report schedules in a time window (dbabrain's time-window fields); readers kept to the window |
| 1.12.1 | `deployed` | working tree `3c7cb43f0b18` (uncommitted) | 2026-11-27T05:45Z, sha256 `36d0305c…b2dbed6f` | 2026-11-27T05:50Z (image `45c559bd7558`, 1.12.0 + the wheel; no migration; dump before) | - (waits for the operator's word) | A sign-in link's dashboard sends the session for its token |

---

## 2. Statuses

`pre-rule` (released before this procedure) · `deployed` (running on the worker, nothing public yet) ·
`released` (tag pushed and on PyPI, row frozen) · `abandoned` (never published, number skipped).

## 3. The rules

1. **The version lives in one place:** `pyproject.toml`. Each release is one commit named
   `Release X.Y.Z`, whose body says what changed for users.
2. **The wheel is built from the commit, not from the working tree:** a clean export with LF line
   endings (`git -c core.autocrlf=false archive <commit>`). A plain `git archive` on Windows gives
   CRLF files.
3. **Nothing is removed on the worker.** The previous version's images, wheel and `.env.bak-<old>`
   stay: they are the way back.
4. **Nothing goes public without the operator's word.** Pushing `main` and the tag publishes to PyPI,
   and a version number cannot be uploaded twice.
5. **A version whose content changes after its wheel is built is rebuilt** under the same number
   only while it is not public; once public, the next number is used.

## 4. The steps

### A. Commit gate - on the build PC

- The full test suite passes (`pytest`). The count of passed tests goes in the sheet.
- `pyproject.toml` carries the new version; the commit `Release X.Y.Z` holds only that version's
  work (no stray files, no internal names).
- Since 1.7.0: `drscore/CHANGELOG.md` and `drscore/CHANGELOG.vi.md` start with that version (`## X.Y.Z — YYYY-MM-DD`,
  lines for users, `[admin]` / `[designer]` marks); the portal shows them (version in the menu, "What's new").
  `tests/test_changelog_system.py` fails without them.
- The sheet `TEST_VERSION_BEFORE_RELEASE_DETAIL_X_Y_Z.md` is opened and the ledger row appended
  with status `deployed` pending.

### B. Build the wheel

- Export the commit with LF endings, build with `pip wheel --no-deps`.
- **Pass:** one `drscore-X.Y.Z-py3-none-any.whl`; its sha256 goes in the sheet and the ledger.

### C. Deploy on the worker

- Copy the wheel into `image/` beside the earlier ones.
- Build `drscore:X.Y.Z` (Dockerfile.wheel) and `drscore-msodbc:X.Y.Z` (Dockerfile.msodbc).
- Copy `.env` to `.env.bak-<previous>`, set `DRSCORE_VERSION=X.Y.Z`, `docker compose up -d drscore`.
  `serve` runs the migrations itself.
- **Pass:** `/healthz` answers `{"status":"ok",...}` and the container runs the new image. Image ids
  and the start time go in the sheet.

### D. Check on the worker

- Sign in, open a report and run it, open its BI page and its Dashboard tab.
- Check what the version changed, in the browser, and say how in the sheet.
- **Pass:** nothing in the logs but requests; the change visible.

### E. Publish - only on the operator's word

- Push `main`, then tag `vX.Y.Z` and push the tag. The GitHub workflow builds, publishes to PyPI
  (trusted publisher) and makes the GitHub release.
- **Pass:** the version is on PyPI; its upload time fills "Public release" and the row becomes
  `released`.

### Rollback

Copy `.env.bak-<previous>` back to `.env` and `docker compose up -d drscore`: the previous images are
still there. A migration added by the new version is not undone by this; the sheet says whether
the version has one.
