"""The side menu, one model for the portal's pages and the admin pages (templates/_shell.html draws it in both).

An administrator sees one menu wherever they are: the reports, the tools, then the administration (the admin pages'
tables and the portal's system pages, grouped by what they are for) and the monitoring (feedback, logs). Each item
is an icon on a coloured tile (kit 1.7.0, ``data-sk-color``); a report group keeps the same colour in the menu and
on the home page.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

# The kit's tile colours a report group takes in turn (kit-bs.css, data-sk-color).
GROUP_COLORS = ("blue", "green", "amber", "purple", "cyan", "pink", "teal", "orange", "indigo", "red", "lime")


def group_color(index: int) -> str:
    return GROUP_COLORS[index % len(GROUP_COLORS)]


@dataclass
class Link:
    label: str
    href: str
    active: bool = False
    badge: int = 0


@dataclass
class Item:
    key: str
    label: str
    icon: str  # a Tabler Icons name, without "ti-"
    color: str  # a kit tile colour
    href: str | None = None  # a plain link; None for a group of links
    links: list[Link] = field(default_factory=list)
    active: bool = False
    badge: int = 0


@dataclass
class Section:
    key: str  # home, reports, tools, admin, monitor
    label: str | None  # None: no heading (the home link)
    items: list[Item]


def admin_url(identity: str) -> str:
    """The list page of an admin table (sqladmin's view identity, e.g. "report-group")."""
    return f"/admin/{identity}/list"


def _admin_link(t: Callable[..., str], path: str, identity: str, key: str) -> Link:
    return Link(t(key), admin_url(identity), active=path.startswith(f"/admin/{identity}/"))


def _page_link(t: Callable[..., str], path: str, href: str, label: str, badge: int = 0) -> Link:
    return Link(label, href, active=path == href or path.startswith(href + "/"), badge=badge)


def _group(key: str, label: str, icon: str, color: str, links: list[Link], badge: int = 0) -> Item:
    return Item(key, label, icon, color, links=links, active=any(link.active for link in links), badge=badge)


def build(t: Callable[..., str], path: str, *, groups: list, active_group: str | None = None,
          active_report: str | None = None, is_admin: bool = False, can_design: bool = False,
          ai_shown: bool = False, ai_name: str = "AI", feedback_on: bool = False, pending_drafts: int = 0,
          new_feedback: int = 0) -> list[Section]:
    """The menu for one page. ``groups`` are the report groups the user may open, as dictionaries
    (authz.permissions.menu: code, name, icon, reports [{code, name, ...}])."""
    sections = [Section("home", None, [Item("home", t("nav.home"), "home", "indigo", href="/", active=path == "/")])]

    if groups:
        items = []
        for i, g in enumerate(groups):
            links = [Link(r["name"], f"/reports/{r['code']}", active=active_report == r["code"]) for r in g["reports"]]
            item = _group(f"group-{g['code']}", g["name"], g.get("icon") or "folder", group_color(i), links)
            item.active = item.active or active_group == g["code"]
            items.append(item)
        sections.append(Section("reports", t("nav.reports"), items))

    tools = []
    if can_design:
        tools.append(Item("design", t("design.nav"), "pencil", "purple", href="/design",
                          active=path.startswith("/design"), badge=pending_drafts if is_admin else 0))
    if ai_shown:
        tools.append(Item("ai", ai_name, "sparkles", "pink", href="/ai", active=path == "/ai"))
    if feedback_on and not is_admin:
        tools.append(Item("feedback", t("feedback.mine"), "message-report", "teal", href="/feedback",
                          active=path.startswith("/feedback")))
    if tools:
        sections.append(Section("tools", t("nav.tools"), tools))

    if not is_admin:
        return sections

    sections.append(Section("admin", t("nav.section.admin"), [
        Item("admin-home", t("nav.admin"), "layout-dashboard", "teal", href="/admin/", active=path in ("/admin", "/admin/")),
        _group("setup", t("nav.adm.report_setup"), "table-options", "blue", [
            _admin_link(t, path, "report-group", "nav.adm.report_group"),
            _admin_link(t, path, "report", "nav.adm.report"),
            _admin_link(t, path, "report-param", "nav.adm.param"),
            _admin_link(t, path, "report-column", "nav.adm.column"),
            _admin_link(t, path, "report-row-filter", "nav.adm.row_filter"),
        ]),
        Item("datasources", t("nav.adm.datasources"), "database", "green", href=admin_url("datasource"),
             active=path.startswith("/admin/datasource/")),
        _group("access", t("nav.adm.access"), "users", "amber", [
            _admin_link(t, path, "user", "nav.adm.user"),
            _admin_link(t, path, "role", "nav.adm.role"),
            _admin_link(t, path, "user-attribute", "nav.adm.attribute"),
            _admin_link(t, path, "grant-group", "nav.adm.grant_group"),
            _admin_link(t, path, "grant-report", "nav.adm.grant_report"),
            _admin_link(t, path, "grant-datasource", "nav.adm.grant_datasource"),
        ]),
        _group("sharing", t("nav.adm.sharing"), "share", "cyan", [
            _page_link(t, path, "/system/links", t("links.title")),
            _page_link(t, path, "/system/notices", t("system.notices")),
            _page_link(t, path, "/system/ai", t("system.ai", name=ai_name)),
        ]),
    ]))

    monitor = []
    if feedback_on:
        monitor.append(Item("feedback", t("feedback.title"), "message-report", "orange", href="/feedback",
                            active=path.startswith("/feedback"), badge=new_feedback))
    monitor.append(_group("logs", t("nav.adm.logs"), "history", "slate", [
        _page_link(t, path, "/system/log", t("nav.adm.log_search")),
        _admin_link(t, path, "activity-log", "nav.adm.activity_log"),
        _admin_link(t, path, "access-log", "nav.adm.access_log"),
        _admin_link(t, path, "audit-log", "nav.adm.audit_log"),
        _admin_link(t, path, "ai-usage", "nav.adm.ai_usage"),
        _admin_link(t, path, "notice-log", "nav.adm.notice_log"),
        _admin_link(t, path, "report-snapshot-history", "nav.adm.history"),
        _admin_link(t, path, "report-schedule", "nav.adm.schedule"),
        _admin_link(t, path, "feedback", "nav.adm.feedback_table"),
    ]))
    sections.append(Section("monitor", t("nav.section.monitor"), monitor))
    return sections
