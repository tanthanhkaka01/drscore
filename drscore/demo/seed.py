"""``seed-demo`` (spec section 22): a working system with no external database.

The source data is synthetic. Passwords are generated and printed once, never stored in clear.
"""

from __future__ import annotations

import random
import secrets
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import delete, func, select

from drscore.admin import service as admin
from drscore.db.engine import Database
from drscore.db.models import (
    Datasource,
    Report,
    ReportColumn,
    ReportGroup,
    ReportParam,
    ReportRowFilter,
    Role,
    User,
)
from drscore.settings import AppSettings, PROJECT_ROOT

SOURCE_PATH = "runtime/demo_source.sqlite3"
ACTOR = "seed-demo"
RANDOM_SEED = 327

SURNAMES = ["Nguyễn", "Trần", "Lê", "Phạm", "Hoàng", "Huỳnh", "Phan", "Vũ", "Võ", "Đặng", "Bùi", "Đỗ",
            "Hồ", "Ngô", "Dương", "Lý"]
MIDDLE = ["Văn", "Thị", "Hữu", "Minh", "Thanh", "Ngọc", "Đức", "Thu", "Quốc", "Gia", "Bảo", "Hoài"]
GIVEN = ["An", "Bình", "Châu", "Dũng", "Giang", "Hà", "Hải", "Hạnh", "Hiếu", "Hoa", "Hùng", "Khánh", "Lan",
         "Linh", "Long", "Mai", "Nam", "Nga", "Phong", "Phúc", "Quân", "Quỳnh", "Sơn", "Tâm", "Thảo", "Trang",
         "Trung", "Tuấn", "Uyên", "Vy", "Yến"]
DEPARTMENTS = ["Kế toán", "Nhân sự", "Kinh doanh", "Sản xuất", "Kho vận", "CNTT"]

GROUPS = [("HR", "Nhân sự", "users"), ("ATT", "Chấm công", "clock"), ("MGMT", "Quản trị", "chart-bar")]
REPORT_CODES = ["EMP_LIST", "ATT_DAILY", "SALARY_LIST", "ATT_SUMMARY", "ATT_TREND", "HR_DASHBOARD"]
USERS = ["admin", "hr_aaa", "hr_all", "viewer", "nobody"]


class SeedRefused(Exception):
    pass


# --------------------------------------------------------------------------------------------
# Demo source

def build_source(path: Path, today: date) -> tuple[int, int]:
    """(Re)creates the demo source database. Returns (employees, attendance rows)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    rnd = random.Random(RANDOM_SEED)
    conn = sqlite3.connect(path)
    try:
        conn.executescript("""
            CREATE TABLE employee (
                emp_code TEXT PRIMARY KEY, full_name TEXT NOT NULL, company_code TEXT NOT NULL,
                department TEXT NOT NULL, join_date TEXT NOT NULL, status TEXT NOT NULL,
                base_salary INTEGER NOT NULL);
            CREATE TABLE attendance_day (
                emp_code TEXT NOT NULL REFERENCES employee(emp_code), work_date TEXT NOT NULL,
                work_hours REAL NOT NULL, ot_hours REAL NOT NULL, late_minutes INTEGER NOT NULL,
                PRIMARY KEY (emp_code, work_date));
        """)
        employees = []
        for company, prefix, count in (("AAA", "A", 110), ("B2B", "B", 90)):
            for i in range(1, count + 1):
                name = f"{rnd.choice(SURNAMES)} {rnd.choice(MIDDLE)} {rnd.choice(GIVEN)}"
                joined = date(2015, 1, 1) + timedelta(days=rnd.randrange(0, (today - date(2015, 1, 1)).days))
                status = "RESIGNED" if rnd.random() < 0.08 else "ACTIVE"
                salary = rnd.randrange(80, 450) * 100_000
                employees.append((f"{prefix}{i:03d}", name, company, rnd.choice(DEPARTMENTS),
                                  joined.isoformat(), status, salary))
        conn.executemany("INSERT INTO employee VALUES (?, ?, ?, ?, ?, ?, ?)", employees)
        attendance = []
        for emp in employees:
            for d in range(30):
                day = today - timedelta(days=29 - d)
                weekend = day.weekday() >= 5
                hours = 0.0 if weekend else rnd.choice([8.0, 8.0, 8.0, 7.5, 7.0, 4.0, 0.0])
                ot = 0.0 if weekend or hours < 8 else rnd.choice([0.0, 0.0, 0.0, 1.0, 1.5, 2.0])
                late = 0 if hours == 0 else rnd.choice([0, 0, 0, 0, 5, 10, 15, 30])
                attendance.append((emp[0], day.isoformat(), hours, ot, late))
        conn.executemany("INSERT INTO attendance_day VALUES (?, ?, ?, ?, ?)", attendance)
        conn.commit()
        return len(employees), len(attendance)
    finally:
        conn.close()


# --------------------------------------------------------------------------------------------
# Demo metadata

def _remove_demo_objects(s) -> None:
    s.execute(delete(Report).where(Report.report_code.in_(REPORT_CODES)))
    s.execute(delete(ReportGroup).where(ReportGroup.group_code.in_([g[0] for g in GROUPS])))
    s.execute(delete(Datasource).where(Datasource.datasource_code == "DEMO"))
    s.execute(delete(Role).where(Role.role_code == "HR_STAFF"))
    # An administrator is never removed: "admin" may be the real one, created before the demo.
    s.execute(delete(User).where(User.username.in_(USERS), User.is_admin.is_(False)))


def _report(s, code, name, group, rtype, ds, query, ttl, *, restricted=False, design=None, order=0,
            description=None, bi_table=None, stale=False) -> Report:
    report = Report(report_code=code, report_name=name, group_id=group.group_id, report_type=rtype,
                    datasource_id=ds.datasource_id if query else None, query_text=query,
                    cache_ttl_seconds=ttl, is_restricted=restricted, sort_order=order,
                    html_design_uri=design if rtype == "HTML" else None,
                    bi_design_uri=design if rtype == "BI" else None,
                    description=description, bi_dataset_table=bi_table, serve_stale_on_error=stale,
                    created_by=ACTOR, updated_by=ACTOR)
    s.add(report)
    s.flush()
    return report


def _company_filter(s, report: Report) -> None:
    s.add(ReportRowFilter(report_id=report.report_id, column_name="company_code", attr_name="company_code"))


def _date_params(s, report: Report) -> None:
    s.add(ReportParam(report_id=report.report_id, param_name="from_date", label="Từ ngày", data_type="date",
                      input_kind="input", is_required=True, default_value="@month_start", sort_order=1))
    s.add(ReportParam(report_id=report.report_id, param_name="to_date", label="Đến ngày", data_type="date",
                      input_kind="input", is_required=True, default_value="@today", sort_order=2))


def seed(database: Database, settings: AppSettings, *, force: bool = False,
         today: date | None = None, source_path: Path | None = None) -> dict[str, str]:
    """Creates the demo source and metadata. Returns {username: generated password}.

    ``source_path`` places the demo source elsewhere than runtime/ (tests)."""
    today = today or date.today()
    with database.session() as s:
        existing = s.scalar(select(func.count()).select_from(Report))
        if existing and not force:
            raise SeedRefused(f"The DRS database already holds {existing} report(s). "
                              f"Use --force to replace the demo objects (other reports are kept).")
    build_source(source_path or PROJECT_ROOT / SOURCE_PATH, today)

    passwords: dict[str, str] = {}
    with database.session() as s:
        if force:
            _remove_demo_objects(s)
            s.flush()
        ds = Datasource(datasource_code="DEMO", datasource_name="Demo source (synthetic)", db_type="sqlite",
                        auth_method="none", description="Synthetic HR data for the demo (seed-demo)",
                        database_name=str(source_path) if source_path else SOURCE_PATH)
        s.add(ds)
        groups = {}
        for i, (code, name, icon) in enumerate(GROUPS):
            groups[code] = ReportGroup(group_code=code, group_name=name, icon=icon, sort_order=i + 1)
            s.add(groups[code])
        s.flush()

        emp = _report(
            s, "EMP_LIST", "Danh sách nhân viên", groups["HR"], "GRID", ds,
            "SELECT emp_code, full_name, company_code, department, join_date, status\n"
            "FROM employee\nORDER BY emp_code", 300, order=1,
            description="Toàn bộ nhân viên, lọc theo công ty của người xem.")
        _company_filter(s, emp)
        for i, (field, label, dtype, fmt, width, frozen) in enumerate([
            ("emp_code", "Mã NV", None, "text", 110, True),
            ("full_name", "Họ tên", None, "text", 200, True),
            ("company_code", "Công ty", None, "text", 110, False),
            ("department", "Phòng ban", None, "text", 140, False),
            ("join_date", "Ngày vào làm", "date", "date", 140, False),
            ("status", "Trạng thái", None, "text", 100, False),
        ]):
            s.add(ReportColumn(report_id=emp.report_id, field_name=field, label=label, data_type=dtype,
                               display_format=fmt, width_px=width, is_frozen=frozen, sort_order=i,
                               footer_aggregate="count" if field == "emp_code" else None))

        att = _report(
            s, "ATT_DAILY", "Chấm công theo ngày", groups["ATT"], "GRID", ds,
            "SELECT a.emp_code, e.full_name, e.company_code, a.work_date, a.work_hours, a.ot_hours,\n"
            "       a.late_minutes\n"
            "FROM attendance_day a JOIN employee e ON e.emp_code = a.emp_code\n"
            "WHERE a.work_date BETWEEN :from_date AND :to_date\n"
            "  AND (:company IS NULL OR e.company_code = :company)\n"
            "ORDER BY a.work_date, a.emp_code", 60, order=1, stale=True)
        _company_filter(s, att)
        _date_params(s, att)
        s.add(ReportParam(report_id=att.report_id, param_name="company", label="Công ty", data_type="text",
                          input_kind="select", sort_order=3,
                          options_query="SELECT DISTINCT company_code, 'Công ty ' || company_code\n"
                                        "FROM employee ORDER BY 1"))

        sal = _report(
            s, "SALARY_LIST", "Bảng lương cơ bản", groups["HR"], "GRID", ds,
            "SELECT emp_code, full_name, company_code, department, base_salary\n"
            "FROM employee WHERE status = 'ACTIVE'\nORDER BY emp_code", 300, restricted=True, order=2)
        _company_filter(s, sal)
        for i, (field, label, fmt, agg) in enumerate([
            ("emp_code", "Mã NV", "text", None), ("full_name", "Họ tên", "text", None),
            ("company_code", "Công ty", "text", None), ("department", "Phòng ban", "text", None),
            ("base_salary", "Lương cơ bản", "integer", "sum"),
        ]):
            s.add(ReportColumn(report_id=sal.report_id, field_name=field, label=label, display_format=fmt,
                               align="right" if field == "base_salary" else None, footer_aggregate=agg,
                               sort_order=i))

        summ = _report(
            s, "ATT_SUMMARY", "Tổng hợp chấm công", groups["ATT"], "HTML", ds,
            "SELECT e.company_code, e.department, COUNT(DISTINCT a.emp_code) AS employees,\n"
            "       SUM(a.work_hours) AS work_hours, SUM(a.ot_hours) AS ot_hours,\n"
            "       SUM(a.late_minutes) AS late_minutes\n"
            "FROM attendance_day a JOIN employee e ON e.emp_code = a.emp_code\n"
            "WHERE a.work_date BETWEEN :from_date AND :to_date\n"
            "GROUP BY e.company_code, e.department\nORDER BY e.company_code, e.department",
            300, design="file:html/att_summary.html", order=2)
        _company_filter(s, summ)
        _date_params(s, summ)

        trend = _report(
            s, "ATT_TREND", "Xu hướng chấm công", groups["ATT"], "HTML", ds,
            "SELECT a.work_date, e.company_code, SUM(a.work_hours) AS work_hours\n"
            "FROM attendance_day a JOIN employee e ON e.emp_code = a.emp_code\n"
            "GROUP BY a.work_date, e.company_code\nORDER BY a.work_date", 300, order=3)
        _company_filter(s, trend)

        _report(s, "HR_DASHBOARD", "Dashboard nhân sự", groups["MGMT"], "BI", ds,
                "SELECT company_code, department, status, COUNT(*) AS employees\n"
                "FROM employee GROUP BY company_code, department, status", 3600,
                order=1, bi_table="hr_headcount")

        names = {"admin": "Quản trị viên", "hr_aaa": "Nhân sự AAA", "hr_all": "Nhân sự toàn công ty",
                 "viewer": "Người xem", "nobody": "Không có quyền"}
        existing_users = set(s.scalars(select(User.username).where(User.username.in_(USERS))))
        for username in USERS:
            if username in existing_users:  # kept as it is, password included
                continue
            passwords[username] = secrets.token_urlsafe(12)
            admin.add_user(s, ACTOR, settings, username, names[username], passwords[username],
                           is_admin=username == "admin")
        admin.add_role(s, ACTOR, "HR_STAFF", "Nhân viên nhân sự")
        admin.grant(s, ACTOR, "group", "HR", role_code="HR_STAFF")
        admin.grant(s, ACTOR, "group", "ATT", role_code="HR_STAFF")
        for username in ("hr_aaa", "hr_all"):
            admin.role_member(s, ACTOR, "HR_STAFF", username, add=True)
        admin.attr_set(s, ACTOR, "hr_aaa", "company_code", ["AAA"])
        admin.attr_set(s, ACTOR, "hr_all", "company_code", ["*"])
        admin.grant(s, ACTOR, "report", "SALARY_LIST", username="hr_all")
        admin.grant(s, ACTOR, "report", "EMP_LIST", username="viewer", can_export=False)
    return passwords
