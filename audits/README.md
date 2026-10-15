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
