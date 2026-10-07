# Third-party licences

Licence gate of spec section 4 (requirement R02): a dependency is added only after its licence was
read from the package itself and it is free for commercial use with no feature behind payment.
`tests/test_licenses.py` fails when a dependency of `pyproject.toml` or a folder of
`drs/web/static/vendor/` is missing from this file, or when a licence is not on the allowed list.

Checked on 2026-10-06. "PyPI metadata" means the `License-Expression` / `License` / classifier
fields of the installed wheel (`importlib.metadata`), which is the package's own declaration.

## Python packages - direct dependencies

| Name | Version | Licence | Checked in |
| --- | --- | --- | --- |
| fastapi | 0.142.2 | MIT | PyPI metadata |
| pydantic | 2.13.5 | MIT | PyPI metadata |
| uvicorn | 0.54.0 | BSD-3-Clause | PyPI metadata |
| jinja2 | 3.1.6 | BSD-3-Clause | PyPI metadata (classifier "BSD License"), LICENSE.txt |
| python-multipart | 0.0.32 | Apache-2.0 | PyPI metadata |
| sqlalchemy | 2.1.3 | MIT | PyPI metadata |
| alembic | 1.20.0 | MIT | PyPI metadata |
| psycopg | 3.3.6 | LGPL-3.0-only | PyPI metadata |
| psycopg-binary | 3.3.6 | LGPL-3.0-only | PyPI metadata |
| pyodbc | 5.3.0 | MIT | PyPI metadata |
| pymssql | 2.4.3 | LGPL-2.1 | PyPI metadata ("GNU LESSER GENERAL PUBLIC LICENSE"), LICENSE file |
| oracledb | 26.0.1 | UPL-1.0 OR Apache-2.0 | PyPI metadata, LICENSE.txt |
| argon2-cffi | 25.1.0 | MIT | PyPI metadata |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause | PyPI metadata |
| authlib | 1.8.0 | BSD-3-Clause | PyPI metadata |
| httpx | 0.28.1 | BSD-3-Clause | PyPI metadata |
| xlsxwriter | 3.2.9 | BSD-2-Clause | PyPI metadata |
| sqladmin | 0.32.0 | BSD-3-Clause | PyPI metadata `License-Expression`. Admin pages; ships Tabler, jQuery, Select2, Flatpickr, Font Awesome Free (MIT / SIL OFL-1.1 fonts / CC-BY-4.0 icons), served from the package, no CDN |
| wtforms | 3.2.2 | BSD-3-Clause | PyPI metadata (classifier "BSD License"), LICENSE.rst |
| babel | 2.18.0 | BSD-3-Clause | PyPI metadata (classifier "BSD License"), LICENSE |
| itsdangerous | 2.2.0 | BSD-3-Clause | PyPI metadata (classifier "BSD License"), LICENSE.txt |
| greenlet | 3.5.6 | MIT AND PSF-2.0 | PyPI metadata `License-Expression`, LICENSE, LICENSE.PSF. Needed by sqladmin (SQLAlchemy asyncio import) |
| pytest | 9.1.1 | MIT | PyPI metadata (tests only) |
| openpyxl | 3.1.5 | MIT | PyPI metadata (tests only) |

## Python packages - pulled in by the ones above

| Name | Version | Licence | Checked in |
| --- | --- | --- | --- |
| annotated-doc | 0.0.5 | MIT | PyPI metadata |
| annotated-types | 0.8.0 | MIT | PyPI metadata |
| anyio | 4.15.1 | MIT | PyPI metadata |
| argon2-cffi-bindings | 26.1.0 | MIT | PyPI metadata |
| certifi | 2026.7.22 | MPL-2.0 | PyPI metadata |
| cffi | 2.1.1 | MIT-0 | PyPI metadata |
| click | 8.5.0 | BSD-3-Clause | PyPI metadata |
| et_xmlfile | 2.0.0 | MIT | PyPI metadata |
| h11 | 0.16.0 | MIT | PyPI metadata |
| httpcore | 1.0.9 | BSD-3-Clause | PyPI metadata |
| idna | 3.20 | BSD-3-Clause | PyPI metadata |
| iniconfig | 2.3.0 | MIT | PyPI metadata |
| joserfc | 1.7.5 | BSD-3-Clause | PyPI metadata |
| mako | 1.4.3 | MIT | PyPI metadata |
| markupsafe | 3.0.4 | BSD-3-Clause | PyPI metadata |
| opentelemetry-api | 1.45.0 | Apache-2.0 | PyPI metadata |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | PyPI metadata |
| pluggy | 1.6.0 | MIT | PyPI metadata |
| pycparser | 3.0 | BSD-3-Clause | PyPI metadata |
| pydantic-core | 2.46.5 | MIT | PyPI metadata |
| pygments | 2.21.0 | BSD-2-Clause | PyPI metadata |
| starlette | 1.7.0 | BSD-3-Clause | PyPI metadata |
| typing-extensions | 4.16.0 | PSF-2.0 | PyPI metadata |
| typing-inspection | 0.4.4 | MIT | PyPI metadata |

## Front-end libraries (vendored into `drs/web/static/vendor/`)

Each at a pinned version, downloaded from the npm registry, with its licence file copied next to it.
No CDN is used at run time.

| Name | Version | Licence | Checked in |
| --- | --- | --- | --- |
| tabler | 1.6.1 | MIT | npm `@tabler/core` package.json, header of tabler.min.css / .js |
| tabler-icons | 3.49.0 | MIT | npm `@tabler/icons-webfont`, LICENSE file |
| tabulator | 6.6.1 | MIT | npm `tabulator-tables` package.json, LICENSE file |
| echarts | 6.1.0 | Apache-2.0 | npm `echarts` package.json, LICENSE and NOTICE files |
| superset-embedded-sdk | 0.4.0 | Apache-2.0 | npm `@superset-ui/embedded-sdk` package.json; bundles `@superset-ui/switchboard` (Apache-2.0) and `jwt-decode` (MIT) |

## Software used beside DRS (not bundled)

| Name | Licence | Note |
| --- | --- | --- |
| Python | PSF-2.0 | Runtime, 3.11 or newer |
| PostgreSQL | PostgreSQL | DRS database in production |
| SQLite | Public domain | DRS database in design / test (part of Python) |
| Microsoft ODBC Driver 18 (or 17) for SQL Server | Free of charge, proprietary | Default MSSQL driver; `pymssql` is the open-source alternative |
| unixODBC (Linux only) | LGPL-2.1 | Needed by pyodbc on Linux; Windows has ODBC built in |
| Oracle Instant Client | Free of charge, proprietary (Oracle Free Use Terms) | Only for `driver = "thick"` (Oracle 11.2); thin mode needs nothing |
| Apache Superset 6.1.0 | Apache-2.0 | BI layer (`docker/compose.yml`, image `apache/superset:6.1.0`) |
| Docker Engine (Linux) | Apache-2.0 | Containers for PostgreSQL / Superset. Not Docker Desktop. |
| Keycloak | Apache-2.0 | Only if the company has no SSO provider |
