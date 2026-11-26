# audits/

Dated records of what was found, planned and done. Kept out of `docs/` on purpose: `docs/` is the
**current-state** reference, while everything here is a **snapshot of one moment** - findings,
counts and versions are true as of the date in the file and go stale by design.

**Do not update an old audit to match reality.** If the picture changed, write a new one. An audit
that gets edited after the fact stops being evidence of what was found.

This repository is public: no internal IP address, host name, database name, user name or secret
goes into a file here. Name things by their role ("the worker", "a SQL Server datasource").

## Naming

```
YYYYMMDD_audit_<topic>.md    a finding: what was true on that date
YYYYMMDD_plan_<topic>.md     a remediation: what to do about it, and in what order
YYYYMMDD_report_<topic>.md   an execution record: what was changed, when, and what it returned
```

A fourth kind is **undated**, because it is not about a moment at all:

```
UPPERCASE_TOPIC.md           a procedure: how a thing is done or checked, updated in place
```

[`TEST_VERSION_BEFORE_RELEASE.md`](./TEST_VERSION_BEFORE_RELEASE.md) is the release procedure. It
carries the **version ledger**: one row per version, appended, its pending cells filled as they
happen, never rewritten once that version is public. Each version also gets its own sheet,
`TEST_VERSION_BEFORE_RELEASE_DETAIL_X_Y_Z.md`: what it changed and how its build, tests and deploy
went.

**Attachments are not indexed on their own.** A screenshot or a captured log is evidence of one
audit: it carries that audit's date and file name as a prefix and is linked from it.

## Index

| File | Date | Topic | Status |
| --- | --- | --- | --- |
| [`TEST_VERSION_BEFORE_RELEASE.md`](./TEST_VERSION_BEFORE_RELEASE.md) | opened 2026-10-15 | The release procedure (commit, wheel, worker deploy, checks, publish) and the version ledger, backfilled from git for 1.0.0 - 1.4.1 | living |
| [`TEST_VERSION_BEFORE_RELEASE_DETAIL_1_4_2.md`](./TEST_VERSION_BEFORE_RELEASE_DETAIL_1_4_2.md) | 2026-10-15 | 1.4.2: solid status badges readable again (Tabler 1.6 `text-<colour>-fg`); built, tested, deployed on the worker and published | released 2026-10-15T08:40Z |
| [`TEST_VERSION_BEFORE_RELEASE_DETAIL_1_5_0.md`](./TEST_VERSION_BEFORE_RELEASE_DETAIL_1_5_0.md) | 2026-10-22 | 1.5.0: mirrors (source tables copied into the DRS database on a schedule) and public links (a report opened without signing in) | deployed, not published |
| [`TEST_VERSION_BEFORE_RELEASE_DETAIL_1_5_1.md`](./TEST_VERSION_BEFORE_RELEASE_DETAIL_1_5_1.md) | 2026-10-22 | 1.5.1: public links with an HTML design, drawn at once and refreshed in the background; `publiclink set`; an embedded dashboard kept when its token cannot be renewed | deployed, not published |
| [`TEST_VERSION_BEFORE_RELEASE_DETAIL_1_6_0.md`](./TEST_VERSION_BEFORE_RELEASE_DETAIL_1_6_0.md) | 2026-10-24 | 1.6.0: choice lists - a decision a report depends on, ticked on a public link and joined by the report (migration 0016) | deployed, not committed, not published |
| [`TEST_VERSION_BEFORE_RELEASE_DETAIL_1_6_1.md`](./TEST_VERSION_BEFORE_RELEASE_DETAIL_1_6_1.md) | 2026-10-26 | 1.6.1: per-user session days - a view-only account stays signed in 30 days (migration 0017) | deployed, not committed, not published |
| [`TEST_VERSION_BEFORE_RELEASE_DETAIL_1_6_2.md`](./TEST_VERSION_BEFORE_RELEASE_DETAIL_1_6_2.md) | 2026-10-26 | 1.6.2: a link may be public or not - not public, it asks for a sign-in and the reader's own rights (migration 0018) | deployed, not committed, not published |
| [`TEST_VERSION_BEFORE_RELEASE_DETAIL_1_6_3.md`](./TEST_VERSION_BEFORE_RELEASE_DETAIL_1_6_3.md) | 2026-10-26 | 1.6.3: a link's editable dates are date pickers holding the range in use | deployed, not committed, not published |
| [`TEST_VERSION_BEFORE_RELEASE_DETAIL_1_6_4.md`](./TEST_VERSION_BEFORE_RELEASE_DETAIL_1_6_4.md) | 2026-11-09 | 1.6.4: portal and Superset at an IP and a domain at once (other_urls, site_urls) | deployed, not committed, not published |
| [`TEST_VERSION_BEFORE_RELEASE_DETAIL_1_7_0.md`](./TEST_VERSION_BEFORE_RELEASE_DETAIL_1_7_0.md) | 2026-11-09 | 1.7.0: activity log, Góp ý, the AI agent Typhon, Telegram / Zalo notices, encrypted secrets (migration 0019) | not built, not deployed, not committed |
| [`TEST_VERSION_BEFORE_RELEASE_DETAIL_1_7_1.md`](./TEST_VERSION_BEFORE_RELEASE_DETAIL_1_7_1.md) | 2026-11-09 | 1.7.1: a table link is the report page without the menu | deployed, not committed, not published |
| [`TEST_VERSION_BEFORE_RELEASE_DETAIL_1_7_2.md`](./TEST_VERSION_BEFORE_RELEASE_DETAIL_1_7_2.md) | 2026-11-09 | 1.7.2: public links made on a page; default link account | deployed, not committed, not published |
| [`TEST_VERSION_BEFORE_RELEASE_DETAIL_1_8_0.md`](./TEST_VERSION_BEFORE_RELEASE_DETAIL_1_8_0.md) | 2026-11-11 | 1.8.0: public links framed by other sites (per link) | deployed, not committed, not published |
| [`TEST_VERSION_BEFORE_RELEASE_DETAIL_1_9_0.md`](./TEST_VERSION_BEFORE_RELEASE_DETAIL_1_9_0.md) | 2026-11-11 | 1.9.0: grid columns arranged by the reader; exports follow them | deployed, not committed, not published |
