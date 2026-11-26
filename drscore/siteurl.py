"""The addresses the portal and Superset are opened at.

DRS can be reached at more than one address: ``[app] base_url`` (for example the server's IP) and
the ``[app] other_urls`` (for example a domain served over https by a reverse proxy). Nothing in the
code names an address. A page is built for the address its request came in at, which the request
middleware keeps in ``current_origin``. Outside a request (the CLI, the scheduler) ``base_url`` is
used.

Superset is opened by the browser too (the embedded dashboards, the Explore links), so it needs an
address that the browser can use from that page: ``[bi.superset] site_urls`` gives the Superset
address for a portal address, and ``[bi.superset] base_url`` is used for the others. An https page
can show nothing from an http address (mixed content), so an https portal needs an https Superset.
"""

from __future__ import annotations

import contextvars
from urllib.parse import urlsplit

#: ``scheme://host[:port]`` of the request being served, as the browser sees it (behind a trusted
#: reverse proxy, after its X-Forwarded-Proto). ``None`` outside a request.
current_origin: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_origin", default=None)


def origin_of(url: str) -> str:
    """``scheme://host[:port]`` of an address, lower case, without a default port."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if ":" in host:  # IPv6
        host = f"[{host}]"
    port = parts.port
    if port is None or (scheme, port) in (("http", 80), ("https", 443)):
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def check_origin(url: str, what: str) -> str:
    """``url`` must be an http(s) address with a host and no path, query or user. Returns it without
    a trailing slash. The error message names ``what`` (the setting)."""
    value = url.strip().rstrip("/")
    try:
        parts = urlsplit(value)
        parts.port  # noqa: B018 - raises on a bad port
    except ValueError as exc:
        raise ValueError(f"{what}: {url!r} is not an address ({exc})") from None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise ValueError(f"{what}: {url!r} must be http://host or https://host (a port may follow)")
    if parts.path or parts.query or parts.fragment or parts.username or parts.password:
        raise ValueError(f"{what}: {url!r} must be only scheme://host[:port], no path")
    return value


def _app(settings):
    """The ``[app]`` section, from it or from the settings that hold it (AppSettings, Settings)."""
    while not hasattr(settings, "base_url") and hasattr(settings, "app"):
        settings = settings.app
    return settings


def portal_origins(settings) -> list[str]:
    """Every address the portal is opened at: ``base_url`` first, then ``other_urls``, no repeats."""
    section = _app(settings)
    out: list[str] = []
    for url in [section.base_url, *section.other_urls]:
        o = origin_of(url)
        if o not in out:
            out.append(o)
    return out


def portal_url(settings) -> str:
    """The portal's address for what is being built: the request's own address when it is one of
    the portal's addresses, else ``base_url``."""
    here = current_origin.get()
    if here and origin_of(here) in portal_origins(settings):
        return origin_of(here)
    return _app(settings).base_url.rstrip("/")


def superset_url(superset_section, portal: str | None = None) -> str:
    """Superset's address for the browser on a page of the portal address ``portal`` (by default the
    request's): its ``site_urls`` entry, else ``base_url``."""
    here = portal if portal is not None else current_origin.get()
    if here:
        wanted = origin_of(here)
        for site, url in superset_section.site_urls.items():
            if origin_of(site) == wanted:
                return url.rstrip("/")
    return superset_section.base_url.rstrip("/")


def mixed_content(page: str | None, target: str) -> bool:
    """An https page cannot frame or call an http address: the browser blocks it."""
    return bool(page) and page.lower().startswith("https://") and target.lower().startswith("http://")


class SiteSchemeMiddleware:
    """A request that arrives with the host of one of the portal's addresses is served as that
    address, scheme included. A reverse proxy that serves ``https://portal.example.com`` and talks
    plain http to DRS then gets pages whose own links (the admin pages build absolute ones) are
    https, whether or not the proxy sends X-Forwarded-Proto and is listed in forwarded_allow_ips.
    The host decides only how this request's links are written: the address is the client's own."""

    def __init__(self, app, settings) -> None:
        self.app = app
        self.schemes: dict[str, str] = {}
        for origin in portal_origins(settings):
            scheme, netloc = origin.split("://", 1)
            self.schemes[netloc] = scheme
            if scheme == "https" and ":" not in netloc.rsplit("]", 1)[-1]:
                self.schemes.setdefault(f"{netloc}:443", scheme)

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket") and self.schemes:
            host = ""
            for name, value in scope.get("headers") or ():
                if name == b"host":
                    host = value.decode("latin-1").strip().lower()
                    break
            wanted = self.schemes.get(host)
            if wanted:
                scheme = wanted if scope["type"] == "http" else ("wss" if wanted == "https" else "ws")
                if scope.get("scheme") != scheme:
                    scope = dict(scope, scheme=scheme)
        await self.app(scope, receive, send)
