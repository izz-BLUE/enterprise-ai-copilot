"""test_tools.py —— MCP Tool 业务逻辑（happy + error）

V2 §七 / §八 / §十一：
- travel_record_get / invoice_verify happy path
- invoice_verify ownership check（cross-employee reject）
- invoice_verify duplicate / invalid / not-found / empty-args 错误码稳定

设计：业务逻辑走 *_impl 函数（绕开 MCP SDK v2 Pydantic 参数校验），
        MCP 注册正确性走 server.list_tools() 单独验证。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from enterprise_oa_mcp_server.server import build_server
from enterprise_oa_mcp_server.tools import (
    ERR_EMPLOYEE_REQUIRED,
    ERR_INVALID_INPUT,
    ERR_INVOICE_NOT_FOUND,
    ERR_INVOICE_OWNERSHIP,
    invoice_verify_impl,
    travel_record_get_impl,
)


@pytest.fixture(scope="module")
def server():
    return build_server()


def _content_payload(result):
    """CallToolResult -> dict（从第一个 TextContent 解析）。"""
    assert result.content, "Tool 应返回至少一个 ContentBlock"
    text = result.content[0].text
    return json.loads(text)


class TestToolRegistration:
    def test_server_has_two_tools(self, server):
        names = {t.name for t in asyncio.run(server.list_tools())}
        assert names == {"travel_record_get", "invoice_verify"}


class TestTravelRecordGet:
    def test_happy_returns_items(self):
        result = travel_record_get_impl("E10001", limit=10)
        assert result.is_error is False
        payload = _content_payload(result)
        assert payload["success"] is True
        assert len(payload["items"]) == 3
        first = payload["items"][0]
        assert first["trip_id"] == "TRIP-20260818-001"
        assert any(d["invoice_id"] == "INV-001" for d in first["expense_documents"])

    def test_limit_truncates(self):
        result = travel_record_get_impl("E10001", limit=1)
        payload = _content_payload(result)
        assert len(payload["items"]) == 1

    def test_empty_employee_id_rejected_with_business_error(self):
        result = travel_record_get_impl("", limit=10)
        assert result.is_error is True
        payload = _content_payload(result)
        assert payload == {
            "success": False,
            "error_code": ERR_EMPLOYEE_REQUIRED,
            "message": "employee_id 不能为空",
        }

    def test_invalid_limit_rejected(self):
        result = travel_record_get_impl("E10001", limit=0)
        assert result.is_error is True
        payload = _content_payload(result)
        assert payload["error_code"] == ERR_INVALID_INPUT

    def test_unknown_employee_returns_empty_items(self):
        result = travel_record_get_impl("E99999")
        payload = _content_payload(result)
        assert payload["success"] is True
        assert payload["items"] == []


class TestInvoiceVerify:
    def test_happy_valid_invoice(self):
        result = invoice_verify_impl("INV-001", "E10001")
        assert result.is_error is False
        payload = _content_payload(result)
        assert payload == {
            "success": True,
            "invoice_id": "INV-001",
            "valid": True,
            "amount": 1600,
            "category": "HOTEL",
            "duplicate": False,
            "issued_at": "2026-08-19",
            "vendor": "上海如家酒店",
        }

    def test_cross_employee_rejected_with_ownership_error(self):
        """Stress G：employee A 验 employee B 的发票 → MCP ownership reject。"""
        result = invoice_verify_impl("INV-005", "E10001")
        assert result.is_error is True
        payload = _content_payload(result)
        assert payload["success"] is False
        assert payload["error_code"] == ERR_INVOICE_OWNERSHIP

    def test_duplicate_invoice_flag(self):
        result = invoice_verify_impl("INV-004", "E10001")
        payload = _content_payload(result)
        assert payload["duplicate"] is True
        assert payload["duplicate_of"] == "INV-001"

    def test_invalid_invoice_returns_invalid_reason(self):
        result = invoice_verify_impl("INV-006", "E10001")
        payload = _content_payload(result)
        assert payload["valid"] is False
        assert "invalid_reason" in payload

    def test_invoice_not_found(self):
        result = invoice_verify_impl("INV-NOT-EXIST", "E10001")
        assert result.is_error is True
        payload = _content_payload(result)
        assert payload["error_code"] == ERR_INVOICE_NOT_FOUND

    def test_empty_invoice_id_rejected_with_business_error(self):
        result = invoice_verify_impl("", "E10001")
        assert result.is_error is True
        payload = _content_payload(result)
        assert payload["error_code"] == ERR_INVALID_INPUT

    def test_empty_employee_id_rejected_with_business_error(self):
        result = invoice_verify_impl("INV-001", "")
        assert result.is_error is True
        payload = _content_payload(result)
        assert payload["error_code"] == ERR_EMPLOYEE_REQUIRED
