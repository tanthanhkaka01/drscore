"""The error catalogue of spec section 17.

Every failure DRS reports to a user is a ``DRSError`` carrying one of these codes. The text shown
to the user comes from the i18n files (``web/i18n/*.json``, key ``error.<CODE>``); the English text
here is the fallback used by the CLI and by the JSON API when no locale is known.
"""

from __future__ import annotations

from typing import Any

# code -> (HTTP status, English message)
CATALOGUE: dict[str, tuple[int, str]] = {
    "AUTH_REQUIRED": (401, "Please sign in."),
    "INVALID_CREDENTIALS": (401, "The user name or password is not correct."),
    "ACCOUNT_LOCKED": (403, "The account is locked after too many failed sign-ins. Try again later."),
    "ACCOUNT_DISABLED": (403, "The account is disabled."),
    "PASSWORD_CHANGE_REQUIRED": (403, "You must change your password before you can continue."),
    "SSO_USER_NOT_REGISTERED": (403, "Your SSO identity is not registered in DRS."),
    "CSRF_FAILED": (403, "The request could not be verified. Reload the page and try again."),
    "FORBIDDEN": (403, "You do not have access to this report."),
    "EXPORT_NOT_ALLOWED": (403, "You are not allowed to export this report."),
    "REFRESH_NOT_ALLOWED": (403, "You are not allowed to refresh this report."),
    "REPORT_NOT_FOUND": (404, "The report was not found."),
    "VIEW_NOT_AVAILABLE": (404, "This view is not available for this report."),
    "PARAM_INVALID": (422, "One or more parameters are missing or not valid."),
    "DESIGN_NOT_SET": (409, "This report has no design yet. Please contact the administrator."),
    "DESIGN_NOT_FOUND": (409, "The design of this report was not found."),
    "DESIGN_INVALID": (409, "The design of this report is not valid."),
    "SNAPSHOT_GONE": (410, "The data of this request is no longer available. Run the report again."),
    "RESULT_TOO_LARGE": (413, "The report returns more data than allowed."),
    "EXPORT_TOO_LARGE": (413, "Too many rows for an XLSX file. Use CSV instead."),
    "REPORT_MISCONFIGURED": (500, "The report is not configured correctly. Please contact the administrator."),
    "DATASOURCE_UNAVAILABLE": (502, "The data source cannot be reached."),
    "SOURCE_QUERY_FAILED": (502, "The data source returned an error."),
    "BI_ENGINE_UNAVAILABLE": (502, "The BI system cannot be reached right now."),
    "BUSY": (503, "The report is being prepared by another request. Try again in a moment."),
    "SOURCE_TIMEOUT": (504, "The data source took too long to answer."),
}


class DRSError(Exception):
    """A failure with a catalogue code.

    ``detail`` is shown to every user only for ``PARAM_INVALID`` (one message per parameter).
    ``admin_detail`` is the technical reason (SQL error, path, ...) and is shown to admins only.
    """

    def __init__(self, code: str, detail: Any = None, admin_detail: str | None = None):
        if code not in CATALOGUE:
            raise ValueError(f"unknown error code {code}")
        super().__init__(f"{code}: {admin_detail or detail or CATALOGUE[code][1]}")
        self.code = code
        self.detail = detail
        self.admin_detail = admin_detail

    @property
    def http_status(self) -> int:
        return CATALOGUE[self.code][0]

    @property
    def message(self) -> str:
        return CATALOGUE[self.code][1]

    def to_json(self, *, is_admin: bool, message: str | None = None) -> dict[str, Any]:
        """The error body of spec section 16. A non-admin never receives the technical reason."""
        detail = self.detail
        if detail is None and is_admin and self.admin_detail:
            detail = self.admin_detail
        return {"error": {"code": self.code, "message": message or self.message, "detail": detail}}
