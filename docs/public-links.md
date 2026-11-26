# Public links: a report opened without signing in

A **public link** is a secret URL that opens one report, read-only, in any browser, with no DRS
account - a screen on the wall of a meeting room, a reader outside the company. An administrator
makes it on **System -> Public links** (1.7.2) or with the CLI; the URL is shown **once** (DRS keeps
only the SHA-256 of its token).

**On the page (1.7.2).** Choose the report, then: the account the link runs as - by default
`user_public` (`[public] default_user`, made at start-up with no password; the page grants it the
report, with *export* and *refresh* as ticked), the buttons, who may open it (anybody with the
address, or signed-in readers), the view, the title, the expiry, the reload interval, and for each
parameter whether the reader may change it and its fixed or starting value. The list below switches
a link off / on (same address) and between public and sign-in. Everything is audited like the CLI.

```bash
python -m drscore publiclink add HQ_OVERVIEW --as president_view --view bi \
    --param currency=USD --editable currency --title "Group overview" --refresh 900 --expires 2027-06-30
# Public link 3 made for HQ_OVERVIEW (runs as president_view). Its address - shown this once:
# https://drs.example.com/p/2vQ0...
python -m drscore publiclink list
python -m drscore publiclink show 3
python -m drscore publiclink set 3 --report HQ_EXEC_DASH --view html --no-params   # same address
python -m drscore publiclink revoke 3          # or by the first characters of the token
```

| Option | |
| --- | --- |
| `--as USER` | the DRS user whose rights the report runs with - its grants and its row filters, checked at every use. Use a viewer account made for the link, not an administrator: revoking its grant, or disabling it, closes the link at once. |
| `--view bi\|grid\|html` | the embedded Superset dashboard (default), the grid as a plain table (5000 rows at most), or the report's HTML design. |
| `--param NAME=VALUE` | the parameter values the report runs with (the others take their defaults). |
| `--editable a,b` | parameters the reader may change on the page; they are validated like any other input. The bar shows each with the value the report runs with - its default resolved (`@month_start-2m` -> the date) when the link and the reader give none - in an input of its type (a date picker for a date; 1.6.3). |
| `--refresh SECONDS` | the page refreshes itself (a wall screen): a dashboard or a grid reloads the page; an HTML design is fetched again behind the one shown (below). The report's retention decides whether that reads the source again. |
| `--expires` | a date (end of that day, app timezone) or a date-time; default never. |
| `--list CODE` | (1.6.0, view `html`) the reader may tick this **choice list** on the page - the only thing a public reader can change (`docs/choice-lists.md`). |
| `--public yes\|no` | (1.6.2) `yes`, the default: anybody with the address. `no`: the report's own address for signed-in readers - see below. `publiclink set REF --public yes\|no` switches it, the address stays the same. |
| `--frame SITES` | (1.8.0) the other sites whose pages may show the link in a frame (`<iframe>`) - see below. `publiclink set REF --frame ''` back to this portal only. |

## Shown inside another site (1.8.0)

By default a link may be framed by the portal only (`X-Frame-Options: SAMEORIGIN`, CSP `frame-ancestors
'self'`). Per link, the administrator lists the sites whose pages may show it in an `<iframe>` - on
**System -> Public links** (the "Sites that may show it in a frame" box when making a link, and the box in
each row of the list, saved with its button) or with `--frame`:

* `*`: any site.
* Sites separated by commas, spaces or lines: `https://hrms.example.com, http://10.0.0.5:8080`. A path is
  dropped (`https://hrms.example.com/app/` -> `https://hrms.example.com`), `https://*.example.com` covers the
  sub-domains, a host without a scheme (`hrms.example.com`) any scheme. Anything else - a quote, `;`, another
  scheme - is refused, so nothing but sites reaches the header.
* Empty: the portal only, as before.

Such a link's pages (`/p/<token>`, and the HTML design's frame `/p/<token>/html`, whose ancestors are the
same) are sent with `frame-ancestors 'self' <sites>` (or `*`) and **without** `X-Frame-Options`, which
cannot list sites. Every other page of the portal keeps `SAMEORIGIN`. A reverse proxy in front of DRS must
not add `X-Frame-Options` of its own. A dashboard link (`--view bi`) framed by another site also needs
Superset to accept that site as an ancestor of its own frame (`frame-ancestors` in `superset_config.py`); a
sign-in link inside another site's frame depends on the browser sending the DRS session cookie there.

## Public or sign-in (1.6.2)

A link with `--public no` is the same address, the same page and the same refresh, but only for
readers who may see the report:

* Opened without a DRS session, it goes to the sign-in page, which comes back to the link once the
  reader has signed in (a session of [`session_days`](./user-guide-admin.md) keeps them signed in).
* The report runs as **the reader**, with their own grants and row filters - not as `--as`. A
  signed-in reader without a right on the report gets 403, without the report's name or data. The
  access log names the reader.
* `[public] enabled = false` closes the public links only: sign-in links keep working.

```bash
python -m drscore publiclink add HQ_EXEC_DASH --as president_view --view html --refresh 1800 --public no
python -m drscore publiclink set 1 --public no     # an existing link: the address handed out stays
python -m drscore publiclink enable 1 --public no  # a revoked link on again, same address, sign-in only
```

`--as` stays required and checked (the link closes when that user loses the report), but a sign-in
link does not run as it. `publiclink enable` switches a revoked link on again after checking it as
`add` would; it is audited `PUBLICLINK_ENABLE`.

**A table link (`--view grid`, 1.7.1) is the report page itself, without the menu.** The page is the
portal's own (`_report_body.html` + `report.js`): the parameter form - only the parameters the link lets the
reader change (`--editable`), each showing the link's value or its default, a list as a list - the
**Refresh data**, **Print / Save PDF** and **XLSX / CSV / TXT (all rows)** buttons, and the grid with a filter on
each column, sorting and pages. Its calls go to the link: `POST /p/<token>/api/run` (the reader's values only for
the editable parameters, as the link's user; CSRF-free like `/list`, same origin only), `GET
/p/<token>/api/params/<name>/options` (a list that depends on another parameter) and `GET
/p/<token>/export?format=xlsx|csv|txt&<parameters>` (the same run, from the cache while valid, every row).
**Export and refresh follow the grant of the user the link runs as** - for a sign-in link, the reader's own:
`python -m drscore grant report CODE --user LINK_USER --export --refresh` (or `--no-export --no-refresh`).
The HTML and dashboard links keep their own page.

What the page does, and does not:

* For a dashboard or a grid, `GET /p/<token>` runs the report as the link's user (or reads its snapshot) and shows the
  dashboard for **that snapshot**: the guest token comes from `GET /p/<token>/bi-token`, which runs
  the same way and never takes a snapshot from the browser. Filters inside the dashboard (Superset's
  native filters) work as in the portal.
* An **HTML design** (`--view html`) never makes the reader wait for the source. `GET /p/<token>`
  answers at once with a frame; the frame loads `GET /p/<token>/html?cached=1`, the design drawn
  from the newest snapshot the link has - even one out of its retention. When that snapshot is out
  of its retention, and then every `--refresh` seconds, the page loads `GET /p/<token>/html` in a
  hidden frame (it runs the report when the cache has nothing current) and swaps it in once it has
  loaded, keeping the reader's scroll position and the design's own state in its `#hash`. A copy
  that fails is dropped and the shown one stays. The design draws its own title, filters and "data
  as of": the bar above it appears only for editable parameters. A design that keeps its filters
  in `location.hash` (and listens to `hashchange`) keeps them across refreshes.
* Every link that cannot be used - unknown, revoked, expired, its report or user switched off,
  `[public] enabled = false` for a public link - answers the same "not found".
* The page has no menu (a public one, no session either); it is sent with `Cache-Control: no-store`,
  `X-Robots-Tag: noindex` and `Referrer-Policy: no-referrer`. Every view is in the access log as
  user `public:<first characters of the token>`; making and revoking are in the audit log.
* (1.9.0) The reader arranges the table - moves, hides and pins columns; the browser keeps it per report and
  set of columns (Tabulator persistence, local storage) - and the export follows it: `export?...&cols=2,0,3`
  names the grid's columns shown, by their position among the grid's columns, in their order on screen
  (`views.export_columns`). A column the design does not export stays out; one exported but not in the grid
  comes after them; without `cols`, every exported column as before. The portal's
  `/api/reports/<code>/export` takes the same `cols`.
* `publiclink set` changes what a link shows - another report, view, user, values, title, refresh
  or expiry - and keeps its address: a link already handed out goes on working. It is checked again
  as a whole, as `add` checks it, and audited (`PUBLICLINK_SET`). Options not given are kept;
  `--param` replaces all the values, `--no-params` drops them, `--editable ''` and `--title ''` clear.
* Anyone who has the URL sees the report: send it like a password, give it an expiry, revoke it
  when it is no longer needed.

Settings, `app.toml`:

```toml
[public]
enabled = false     # off: every /p/... address answers "not found"
theme = "dark"      # "dark" | "light": the page around the report
locale = ""         # "en" | "vi": the page's language; empty = the reader's, else [app] default_locale
```

A dashboard shown for a snapshot kept only because the source failed (`serve_stale_on_error`) has
no rows: the dataset view reads the snapshots in the cache, not the history. The page keeps a
dashboard already on the screen when a later guest token cannot be had, and asks again a minute
later; an HTML design has no such limit.

For a dashboard the Superset "allowed domains" of its embedding must include the DRS address, as for
the portal (`docs/superset-setup.md`).
