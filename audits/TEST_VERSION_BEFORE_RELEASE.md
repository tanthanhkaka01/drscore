# Release procedure - qualifying a drscore version before it is published

**Undated on purpose.** This is *what* every version goes through, step by step, and what counts as
passing each step. Every version writes its own sheet beside this file and fills one row of §1.
Updated in place when the procedure changes - never to match a run.

- **Per version:** `TEST_VERSION_BEFORE_RELEASE_DETAIL_X_Y_Z.md` holds what that version changed and
  how its build, tests and deploy went; the current one is
  [`…DETAIL_1_4_2.md`](./TEST_VERSION_BEFORE_RELEASE_DETAIL_1_4_2.md). A sheet may add steps; it may
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
