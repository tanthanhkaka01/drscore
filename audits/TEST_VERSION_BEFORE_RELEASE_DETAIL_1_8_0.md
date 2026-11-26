# 1.8.0 - work list

**Opened 2026-11-11, at the operator's word** (ORD 437). The procedure is
[`TEST_VERSION_BEFORE_RELEASE.md`](./TEST_VERSION_BEFORE_RELEASE.md). Built from the private repository (on `ef60212`).

**Why this version exists:** the HR system's website is to show the report links inside its own pages (an iframe).
Every DRS page was sent with `X-Frame-Options: SAMEORIGIN` and CSP `frame-ancestors 'self'`, so a browser refused it
in another site's frame. The operator: *"X-Frame-Option cho tôi * (all) nhé"*; *"thêm 1 field trong drs để cho nhập
text vào, có thể là *, https://<HR site>/, ...."*.

---

## 0. Where 1.8.0 stands

| Item | State |
| --- | --- |
| 1.1 | `drs_public_link.frame_ancestors` (migration 0020, one nullable column): the sites that may frame the link - **done** |
| 1.2 | The link's pages (`/p/<token>`, `/p/<token>/html`) send `frame-ancestors 'self' <sites>` or `*`, without `X-Frame-Options`; every other page as before - **done** |
| 1.3 | System -> Public links: the box when making a link, and a box + save button on each row of the list; CLI `publiclink add\|set --frame` - **done** |
| Tests | 3 new in `test_publiclink.py`; full suite on the build PC (SQLite) (§2) |
| Worker | 1.8.0 since 2026-11-11T01:45Z; the 7 HR report links set to `*` (§3) - **done** |
| Commit / publish | not yet: wait for the operator's word |

No new dependency.

## 1. Items

- `publiclink.frame_ancestors(text)`: `*`, or sites separated by commas / spaces / lines; scheme `http`/`https`
  or none, host (a leading `*.` allowed), optional port; a path is dropped; lower-cased, de-duplicated; anything else
  (a quote, `;`, another scheme) is refused with LinkError, so only sites reach the header. Every item is checked even
  when `*` is among them. `frame_csp()` writes it into a page's CSP.
- `publiclink.create` / `change` take `framers`; `describe` shows `frame`.
- `routes/public.py`: `_headers(..., framers)` and `_html_headers(nonce, framers)` - the HTML design's frame has the
  same ancestors as its page, since `frame-ancestors` checks every ancestor.
- `app.py`: `frames_self_only(csp)`: the middleware adds `X-Frame-Options: SAMEORIGIN` only when the page's own CSP
  frames itself only (X-Frame-Options cannot list sites; a browser reading it would refuse them all).
- `routes/system.py` (`frame` field on create; action `/system/links/<id>/frame`), `system_links.html`, i18n
  `links.frame*` (VI / EN), `cli_publiclink.py` (`--frame`), `docs/public-links.md` ("Shown inside another site"),
  changelog 1.8.0 (VI / EN), `pyproject.toml`.

Not covered (written in the docs): a dashboard link (`bi`) framed by another site also needs Superset's own
`frame-ancestors` to list that site; a sign-in link in another site's frame depends on the browser sending the DRS
session cookie there.

## 2. Tests

| Run | When | Result |
| --- | --- | --- |
| 3 new: the parser (good and refused inputs); headers of a closed / `*` / listed link and of an HTML link's frame, the sign-in page unchanged; an admin sets / clears it on the page, CLI `set --frame` | 2026-11-11 ~01:30Z | passed |
| `test_publiclink.py` | ~01:30Z | 29 passed (`test_cli` failed once: a random token starting with `-` is read as an option by argparse - an old flake, passes on re-run) |
| Full suite, SQLite, build PC | ~01:40Z | 505 passed, 6 skipped, 1 failed = the known Windows symlink test (`test_a10_symlink_out_of_the_root_is_invalid`) |
| `test_default_admin.py` | ~01:35Z | 2 failed - the same on the unchanged tree (stash): an existing failure, not this version's |

## 3. Build and deploy

| Step | When (UTC) | Result |
| --- | --- | --- |
| Wheel from tree `837d3a75` (LF export) | 01:37Z | sha256 `16ba3dd14dc2adaae38ebd4a634c84ed873700697765c3b86e35355bbd0293cd` |
| `drscore-msodbc:1.8.0` = swap on 1.7.2 | 01:45Z | `2f3571f0a5c9` |
| `pg_dump -Fc` before | 01:45Z | `runtime/pre_upgrade_1.8.0/drs.dump`, 232 727 189 bytes |
| `.env` -> `.env.bak-1.7.2`, `docker compose up -d drscore` | 01:45Z | running 1.8.0, migrated 0019 -> 0020, healthz ok |
| The 7 HR report links: `publiclink set <id> --frame '*'` | ~01:47Z | `frame: "*"` |
| Headers at the domain, no sign-in | ~01:48Z | the 7 links: 200, `frame-ancestors *`, no `X-Frame-Options`; `/login` still `SAMEORIGIN` |
| Headless Edge: a page on another origin (a local server) with each link in an iframe | ~01:55Z | all 7 load and show the report page with data |

Seen while testing: a page served as a *public* site framing the domain (which resolves to a private address on the
LAN) is refused by Edge / Chrome with `ERR_BLOCKED_BY_LOCAL_NETWORK_ACCESS_CHECKS` - the browser's Local Network
Access rule, not DRS. The HR site resolves to the same internal proxy, so it is private to private and allowed.

**Rollback:** `.env.bak-1.7.2`, `docker compose up -d drscore`. Migration 0020 only adds a nullable column: 1.7.2
runs with it in place.

## 4. Publish

Not published.
