# 1.4.2 - work list

**Opened 2026-10-15, at the operator's word.** The procedure is
[`TEST_VERSION_BEFORE_RELEASE.md`](./TEST_VERSION_BEFORE_RELEASE.md); this is the first version
recorded under it (1.0.0 - 1.4.1 are `pre-rule` rows of its ledger).

**Why this version exists:** while the designer user guide was being illustrated with screenshots of
the worker, the draft status badges could not be read: "Approved" was green text on green, "Draft"
grey on grey. The operator, 2026-10-15 (translated): *fix it*, then *deploy it and retake the
screenshots*.

---

## 0. Where 1.4.2 stands

| Item | State |
| --- | --- |
| 1.1 | solid status badges readable again - **done 2026-10-15** (`656f0d9`) |
| Build | wheel built from the LF export of `656f0d9` - **done** |
| Tests | full suite 412 passed, 6 skipped, 1 failed (a Windows symlink privilege, not the code) - see §2 |
| Worker | `drscore-msodbc:1.4.2` running since 2026-10-15T08:24:20Z, `/healthz` ok - **done** |
| Check | badges read on the worker in the browser, guide screenshots retaken - **done** |
| Publish | `main` pushed and `v1.4.2` tagged at the operator's word; on PyPI 2026-10-15T08:40:18Z - **released** |

---

## 1. Items

### 1.1 Solid status badges are readable - **asked 2026-10-15, done the same day** (`656f0d9`)

**What was seen.** On *Report design* the status column, and on a draft page the status next to the
title, showed a coloured pill with no legible word. In dark mode the same.

**Cause.** Tabler 1.6.1 gives every `.badge` the secondary text colour
(`--tblr-badge-color: var(--tblr-secondary)`), and `bg-<colour>` sets only the background. Tabler's
own pattern for a solid badge is `bg-<colour> text-<colour>-fg`; the templates had only the first
half. The `-lt` badges (`bg-green-lt` ...) set both and were never affected.

**Fix.** `text-<colour>-fg` added to every solid badge - 15, in three templates:

| Template | Badges |
| --- | --- |
| `design_list.html` | draft status: Draft, Pending, Approved, Rejected |
| `design_edit.html` | draft status next to the title; the `+` / `-` / `~` badges of a proposed change (parameters and columns) |
| `base.html` | the pending-drafts counter in the menu |

No CSS of our own was added; `drs.css` is unchanged. A search for `badge bg-<colour>` without `-fg`
now finds nothing under `drscore/`.

**Checked.**
- Rendered before / after with the bundled `tabler.min.css`, light and dark: white text on every
  colour after, unreadable before.
- On the worker after the deploy: the computed text colour of the Approved badges on *Report design*
  is `oklch(0.9851 0 0)` (near white). The two user-guide screenshots that showed the fault were
  retaken.

**Migration:** none. Rollback to 1.4.1 is a plain `.env` switch.

---

## 2. Tests

| Run | When (UTC) | Result |
| --- | --- | --- |
| `tests/test_designer.py`, `tests/test_selfservice.py` (before the commit) | 2026-10-15 | 37 passed |
| Full suite on `656f0d9` (Windows build PC, SQLite) | 2026-10-15T08:27Z | 412 passed, 6 skipped, **1 failed** in 196 s |

**The one failure is the build PC, not the code:** `test_designs.py::test_a10_symlink_out_of_the_root_is_invalid[sqlite]`
cannot create its symlink - `OSError: [WinError 1314] A required privilege is not held by the client`
(Windows allows symlinks only with Developer Mode or as administrator). The test touches no template.
Among the 6 skips are the server-engine tests whose connection variables are not set on the build PC
(`DRS_TEST_MSSQL`, `DRS_TEST_PG_URL`); the run's output kept only those reasons.

---

## 3. Build and deploy

| Step | When (UTC) | Result |
| --- | --- | --- |
| Commit `Release 1.4.2` | 08:22:49Z | `656f0d9`: 3 templates + `pyproject.toml` (16 lines changed) |
| Wheel from `git -c core.autocrlf=false archive HEAD` | 08:23Z | `drscore-1.4.2-py3-none-any.whl`, 2 977 589 bytes, sha256 `800228e48d130b92237617c1c10684c8ded74d837372ba73296e22d24287e6f5`; no CR in the exported templates |
| Wheel copied to the worker `image/` | 08:23:18Z | beside the 1.1.1 - 1.4.1 wheels |
| `drscore:1.4.2` (Dockerfile.wheel) | 08:23:42Z | `sha256:2e93bd90b879…f96c045942`; also tagged `drscore:latest` |
| `drscore-msodbc:1.4.2` (Dockerfile.msodbc) | 08:24:10Z | `sha256:e8a20ee7ef61…ef9e32fd7ac4` |
| `.env` copied to `.env.bak-1.4.1`, `DRSCORE_VERSION=1.4.2` | 08:24Z | - |
| `docker compose up -d drscore` | 08:24:20Z | container recreated on `drscore-msodbc:1.4.2` |
| `/healthz` | 08:24Z | `{"status":"ok","database":"postgresql"}` (the first probe hit the restart) |

**What the worker ran before:** 1.4.1, and its installed package matched the 1.4.1 commit file for
file except the three templates above - checked before the build, so 1.4.2 ships nothing beyond
this sheet.

**Kept for rollback:** the 1.4.1 (and older) images and wheels, `.env.bak-1.4.1`. Nothing removed.

---

## 4. Publish

At the operator's word (2026-10-15, translated: *do both* - publish, and commit the public docs).

| Step | When (UTC) | Result |
| --- | --- | --- |
| `git push origin main` | 08:39Z | `4bcba6f..656f0d9` |
| Tag `v1.4.2` (annotated, "drscore 1.4.2") on `656f0d9`, pushed | 08:39:33Z | the release workflow started |
| Workflow `release` | 08:39Z - 08:41Z | completed, success |
| PyPI `drscore 1.4.2` | 08:40:18Z (wheel), 08:40:20Z (sdist) | wheel sha256 `d6fcde4fdfed2309…`, sdist `8a84bb9d8baebe69…` |

**The PyPI wheel is not byte for byte the worker's wheel:** the workflow builds its own from the same
commit `656f0d9` (other machine, other timestamps), so its sha256 differs from `800228e4…` above.
The content is the same commit.
