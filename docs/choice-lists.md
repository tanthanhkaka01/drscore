# Choice lists: choices ticked on a page, joined by the reports

Some rules of a report are a decision, not a formula: *which kinds of equipment count as IT spend*,
*which cost centres belong to the plant*. A **choice list** keeps such a decision out of the
report's SQL. The list is defined once by an administrator, the person who decides ticks it on a
page opened by a public link, and the report joins the ticked items like any table. A tick saved on
the page is in the reports' table at once (a report's cache keeps its last result until it expires).

DRS 1.6.0. Lists are kept in `drs_choice_list` / `drs_choice_item`; every change of a tick is audited.

## The list and its table

A list is imported from JSON (`choicelist import`), and written - after every import and every tick -
into a table of the **mirror schema** (`[postgresql] mirror_schema`, the schema of the mirrors, see
`docs/mirror.md`), readable by `[mirror] read_role`. A report on the mirror datasource joins it:

| Column | |
| --- | --- |
| `item_key` (primary key) | the key the report joins on |
| `label`, `group_name`, `sort_order` | as defined |
| `is_checked` | ticked or not |
| `changed_at`, `changed_by` | the last change of that item's tick (UTC; the CLI user, or `public:<token hint>`) |

```json
{"code": "IT_DEVICE_TYPE", "title": "Which kinds of equipment count as IT", "table": "list_it_device_type",
 "description": "Ticked: counted in IT spend and in the IT device counts.",
 "items": [
   {"key": "laptop", "label": "Laptop", "group": "Computers", "detail": "Notebooks, MacBooks", "checked": true},
   {"key": "tv", "label": "TV", "group": "Displays", "detail": "Smart TVs in meeting rooms", "checked": false}]}
```

```bash
python -m drscore choicelist import it_types.json      # add or describe again; a decided tick is kept
python -m drscore choicelist import it_types.json --reset   # ... and set every tick as the file says
python -m drscore choicelist list
python -m drscore choicelist show IT_DEVICE_TYPE        # [x] / [ ] per item, who changed it last
python -m drscore choicelist tick IT_DEVICE_TYPE --on tv --off phone
python -m drscore choicelist export IT_DEVICE_TYPE --file it_types.json
python -m drscore choicelist publish IT_DEVICE_TYPE     # write its table again
```

* An import **keeps the tick of an item already in the list** - the file's `checked` only ticks a new
  item - unless `--reset`. An item no longer in the file is removed. Keys: letters, digits, `_ . -`.
* The table name is a plain lower-case name; a mirror's table or another list's is refused.
* A list is never deleted; `"active": false` in the file switches it off (its link can no longer tick it).

## Ticking it on a page

A public link of the HTML view may be allowed to tick **one** list (`docs/public-links.md`):

```bash
python -m drscore publiclink add IT_TYPE_CHECK --as it_view --view html --list IT_DEVICE_TYPE --title "What counts as IT"
python -m drscore publiclink set 5 --list IT_DEVICE_TYPE    # an existing link; --list '' takes it away
```

The report's HTML design draws the page; it reads and saves the list at one more address of the link,
next to its own (`/p/<token>/html` -> `/p/<token>/list`):

* `GET /p/<token>/list` - the list now, not from a snapshot: `{code, title, description, changed_at,
  changed_by, items: [{key, label, group, detail, checked, changed_at}]}`.
* `POST /p/<token>/list` with `{"checked": {"tv": true, "phone": false}}` - saves those ticks, writes the
  table, answers the list as GET does plus `changed` (the keys that changed).

```js
const listUrl = location.pathname.replace(/\/html$/, '/list');
fetch(listUrl, {method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({checked: {tv: false}}), credentials: 'omit'});
```

The secret address is the right to tick: anyone who has it can change that list, and nothing else.
DRS takes a save only as JSON (a form of another site cannot send JSON without the browser asking
first), only with an `Origin` of the DRS server when the browser sends one, only for items of that
list; a link revoked or expired, or `[public] enabled = false`, answers "not found". Each change is in
`drs_audit_log` (`CHOICELIST_TICK`: the link's token hint, the reader's address, before / after).

A page that keeps a reader's unsaved ticks in its address `#hash` keeps them when the public page
swaps in a fresher copy of the design (`docs/public-links.md`, "HTML design").
