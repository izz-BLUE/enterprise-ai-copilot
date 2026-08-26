"""test_enterprise_expense_status_tool.py —— expense_status_tool 测试

V2 §二十四：Java 权威状态、身份由 Executor 注入、跨员工读取被拒。
"""

from __future__ import annotations

import json
from unittest.mock import patch

from app.clients.java_client import JavaClientError
from app.tools.enterprise_tools import expense_status_tool


def _invoke(expense_id, employee_id="E10001", trace_id="trace-1"):
    return json.loads(expense_status_tool.invoke({
        "expense_id": expense_id,
        "employee_id": employee_id,
        "trace_id": trace_id,
    }))


class TestExpenseStatusTool:
    def test_happy_returns_java_authority(self):
        with patch(
            "app.tools.enterprise_tools.get_java_client"
        ) as client:
            client.return_value.get_expense_status.return_value = {
                "expenseId": "EXP-20260826-000001",
                "status": "SUBMITTED",
                "claimedAmount": 1830,
                "reimbursableAmount": 1730,
                "tripId": "TRIP-20260818-001",
                "submittedAt": "2026-08-26T10:00:00Z",
            }
            out = _invoke("EXP-20260826-000001")
        assert out["success"] is True
        assert out["expense_id"] == "EXP-20260826-000001"
        assert out["status"] == "SUBMITTED"
        assert out["claimed_amount"] == 1830
        assert out["reimbursable_amount"] == 1730
        assert out["trip_id"] == "TRIP-20260818-001"
        assert out["source"] == "java"

    def test_missing_expense_id_rejected(self):
        out = _invoke("")
        assert out["success"] is False
        assert out["error_code"] == "EXPENSE_ID_REQUIRED"

    def test_missing_employee_id_rejected(self):
        out = _invoke("EXP-20260826-000001", employee_id="")
        assert out["success"] is False
        assert out["error_code"] == "EMPLOYEE_ID_REQUIRED"

    def test_java_error_propagates(self):
        with patch(
            "app.tools.enterprise_tools.get_java_client"
        ) as client:
            client.return_value.get_expense_status.side_effect = JavaClientError(
                "EXPENSE_NOT_FOUND", "未找到报销单。", 404)
            out = _invoke("EXP-NOT-EXIST")
        assert out["success"] is False
        assert out["error_code"] == "EXPENSE_NOT_FOUND"

    def test_cross_employee_read_rejected(self):
        """V2 §二十四：Java 端 ownership check 拒绝跨员工读取。"""
        with patch(
            "app.tools.enterprise_tools.get_java_client"
        ) as client:
            client.return_value.get_expense_status.side_effect = JavaClientError(
                "EXPENSE_NOT_FOUND", "未找到报销单。", 404)
            out = _invoke("EXP-20260826-000001", employee_id="E10002")
        assert out["success"] is False
        assert out["error_code"] == "EXPENSE_NOT_FOUND"
