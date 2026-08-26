"""fixtures.py —— Enterprise OA MCP server 内存 fixture

V2 §七：
- travel_record_get：3 条出差记录 + 每条 trip 携带关联的 expense_documents
  （含 invoice_id / category / declared_amount）。invoice reference 不代表
  验真，仍需经过 invoice_verify_tool。
- invoice_verify：5 张发票，覆盖 valid / duplicate / invalid / 他员工 4 种状态。

错误统一：
  {"success": False, "error_code": "...", "message": "..."}
"""

from __future__ import annotations

# 出差记录（按 employee_id 索引）。
# 每条 trip 携带关联 expense_documents（不是验真，只是凭证参考）。
_TRAVEL_FIXTURES: dict[str, list[dict]] = {
    "E10001": [
        {
            "trip_id": "TRIP-20260818-001",
            "employee_id": "E10001",
            "destination": "上海",
            "start_date": "2026-08-18",
            "end_date": "2026-08-20",
            "purpose": "客户拜访",
            "status": "APPROVED",
            "expense_documents": [
                {
                    "invoice_id": "INV-001",
                    "category": "HOTEL",
                    "declared_amount": 1600,
                    "description": "上海如家 2 晚",
                },
                {
                    "invoice_id": "INV-002",
                    "category": "TAXI",
                    "declared_amount": 230,
                    "description": "机场往返打车",
                },
            ],
        },
        {
            "trip_id": "TRIP-20260701-002",
            "employee_id": "E10001",
            "destination": "北京",
            "start_date": "2026-07-01",
            "end_date": "2026-07-02",
            "purpose": "总部汇报",
            "status": "APPROVED",
            "expense_documents": [],
        },
        {
            "trip_id": "TRIP-20260610-003",
            "employee_id": "E10001",
            "destination": "深圳",
            "start_date": "2026-06-10",
            "end_date": "2026-06-12",
            "purpose": "供应商洽谈",
            "status": "PENDING",
            "expense_documents": [
                {
                    "invoice_id": "INV-006",
                    "category": "MEAL",
                    "declared_amount": 480,
                    "description": "客户晚宴",
                },
            ],
        },
    ],
    "E10002": [
        {
            "trip_id": "TRIP-20260820-101",
            "employee_id": "E10002",
            "destination": "杭州",
            "start_date": "2026-08-20",
            "end_date": "2026-08-22",
            "purpose": "团队建设",
            "status": "APPROVED",
            "expense_documents": [
                {
                    "invoice_id": "INV-005",
                    "category": "HOTEL",
                    "declared_amount": 1450,
                    "description": "杭州西湖酒店",
                },
            ],
        },
    ],
}

# 发票验真表（按 invoice_id 索引）。每张发票带 owner_employee_id。
# INV-001/002/003 属于 E10001；INV-004 duplicate；INV-005 属于 E10002；
# INV-006 invalid（金额不一致）。
_INVOICE_FIXTURES: dict[str, dict] = {
    "INV-001": {
        "invoice_id": "INV-001",
        "owner_employee_id": "E10001",
        "valid": True,
        "amount": 1600,
        "category": "HOTEL",
        "duplicate": False,
        "issued_at": "2026-08-19",
        "vendor": "上海如家酒店",
    },
    "INV-002": {
        "invoice_id": "INV-002",
        "owner_employee_id": "E10001",
        "valid": True,
        "amount": 230,
        "category": "TAXI",
        "duplicate": False,
        "issued_at": "2026-08-20",
        "vendor": "上海强生出租",
    },
    "INV-003": {
        "invoice_id": "INV-003",
        "owner_employee_id": "E10001",
        "valid": True,
        "amount": 800,
        "category": "MEAL",
        "duplicate": False,
        "issued_at": "2026-08-19",
        "vendor": "上海小南国",
    },
    "INV-004": {
        "invoice_id": "INV-004",
        "owner_employee_id": "E10001",
        "valid": True,
        "amount": 1200,
        "category": "HOTEL",
        "duplicate": True,
        "duplicate_of": "INV-001",
        "issued_at": "2026-08-20",
        "vendor": "上海如家酒店",
    },
    "INV-005": {
        "invoice_id": "INV-005",
        "owner_employee_id": "E10002",
        "valid": True,
        "amount": 1450,
        "category": "HOTEL",
        "duplicate": False,
        "issued_at": "2026-08-21",
        "vendor": "杭州西湖酒店",
    },
    "INV-006": {
        "invoice_id": "INV-006",
        "owner_employee_id": "E10001",
        "valid": False,
        "amount": 200,
        "category": "MEAL",
        "duplicate": False,
        "issued_at": "2026-06-11",
        "vendor": "客户晚宴餐厅",
        "invalid_reason": "申报金额与开票金额不一致",
    },
}


def list_trips_for_employee(employee_id: str) -> list[dict]:
    return [dict(t) for t in _TRAVEL_FIXTURES.get(employee_id, [])]


def get_invoice(invoice_id: str) -> dict | None:
    inv = _INVOICE_FIXTURES.get(invoice_id)
    return dict(inv) if inv else None
