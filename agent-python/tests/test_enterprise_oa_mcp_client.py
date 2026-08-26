"""test_enterprise_oa_mcp_client.py —— MCP Client Adapter 行为

V2 §八：tool 看不到 transport / session / JSON-RPC 协议细节；
       错误归一化为 4 类。
V2 §十一：invoice_verify_tool 强制 identity_required=true。
V2 §八：仅 travel/invoice 允许 1 次 transport retry；Tool 业务错误不重试。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.integrations.mcp import enterprise_oa_client as client_module
from app.integrations.mcp.enterprise_oa_client import (
    OA_MCP_INVALID_RESPONSE,
    OA_MCP_TIMEOUT,
    OA_MCP_TOOL_ERROR,
    OA_MCP_UNREACHABLE,
    OaMcpClientError,
    get_enterprise_oa_client,
    reset_enterprise_oa_client,
)
from app.tools.enterprise_tools import (
    invoice_verify_tool,
    travel_record_tool,
)


class _FakeClient:
    """Fake EnterpriseOaClient for Tool testing."""

    def __init__(self):
        self.travel_calls = []
        self.invoice_calls = []
        self.travel_response = {"success": True, "items": []}
        self.invoice_response = {"success": True, "valid": True, "amount": 100}

    def travel_record_get(self, *, employee_id, limit=10):
        self.travel_calls.append({"employee_id": employee_id, "limit": limit})
        return self.travel_response

    def invoice_verify(self, *, invoice_id, employee_id):
        self.invoice_calls.append({"invoice_id": invoice_id, "employee_id": employee_id})
        return self.invoice_response


@pytest.fixture
def fake_client():
    fake = _FakeClient()
    reset_enterprise_oa_client()
    client_module._client_singleton = fake
    yield fake
    reset_enterprise_oa_client()


class TestSingletonLifecycle:
    def test_default_singleton_is_mcp_client(self):
        reset_enterprise_oa_client()
        c = get_enterprise_oa_client()
        assert type(c).__name__ == "McpEnterpriseOaClient"
        reset_enterprise_oa_client()


class TestTravelRecordTool:
    def test_happy_returns_items(self, fake_client):
        fake_client.travel_response = {
            "success": True,
            "items": [
                {
                    "trip_id": "TRIP-1",
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
                            "description": "酒店",
                        },
                    ],
                }
            ],
        }
        out = travel_record_tool.invoke({
            "employee_id": "E10001",
            "trace_id": "trace-1",
            "limit": 10,
        })
        import json
        payload = json.loads(out)
        assert payload["success"] is True
        assert payload["total"] == 1
        assert payload["items"][0]["trip_id"] == "TRIP-1"
        assert payload["source"] == "mcp:enterprise_oa"
        assert fake_client.travel_calls == [
            {"employee_id": "E10001", "limit": 10}
        ]

    def test_missing_employee_id_returns_identity_error(self, fake_client):
        out = travel_record_tool.invoke({"employee_id": "", "trace_id": "t"})
        import json
        payload = json.loads(out)
        assert payload["success"] is False
        assert payload["error_code"] == "EMPLOYEE_ID_REQUIRED"
        assert fake_client.travel_calls == []

    def test_mcp_tool_error_passes_through(self, fake_client):
        fake_client.travel_response = {
            "success": False,
            "error_code": "OA_MCP_INVOICE_OWNERSHIP",
            "message": "forbidden",
        }
        out = travel_record_tool.invoke({
            "employee_id": "E10001",
            "trace_id": "t",
            "limit": 10,
        })
        import json
        payload = json.loads(out)
        assert payload["success"] is False
        assert payload["error_code"] == "OA_MCP_INVOICE_OWNERSHIP"


class TestInvoiceVerifyTool:
    def test_happy_returns_verified_fields(self, fake_client):
        fake_client.invoice_response = {
            "success": True,
            "invoice_id": "INV-001",
            "valid": True,
            "amount": 1600,
            "category": "HOTEL",
            "duplicate": False,
            "issued_at": "2026-08-19",
            "vendor": "上海如家",
        }
        out = invoice_verify_tool.invoke({
            "invoice_id": "INV-001",
            "employee_id": "E10001",
            "trace_id": "t",
        })
        import json
        payload = json.loads(out)
        assert payload["success"] is True
        assert payload["invoice_id"] == "INV-001"
        assert payload["valid"] is True
        assert payload["amount"] == 1600
        assert payload["category"] == "HOTEL"
        assert payload["duplicate"] is False
        assert fake_client.invoice_calls == [
            {"invoice_id": "INV-001", "employee_id": "E10001"}
        ]

    def test_cross_employee_ownership_reject_propagates(self, fake_client):
        """Stress G 客户端侧：跨员工 invoice 验真失败 → error_code 透传。"""
        fake_client.invoice_response = {
            "success": False,
            "error_code": "OA_MCP_INVOICE_OWNERSHIP",
            "message": "forbidden",
        }
        out = invoice_verify_tool.invoke({
            "invoice_id": "INV-005",
            "employee_id": "E10001",
            "trace_id": "t",
        })
        import json
        payload = json.loads(out)
        assert payload["success"] is False
        assert payload["error_code"] == "OA_MCP_INVOICE_OWNERSHIP"

    def test_missing_invoice_id_rejected(self, fake_client):
        out = invoice_verify_tool.invoke({
            "invoice_id": "",
            "employee_id": "E10001",
            "trace_id": "t",
        })
        import json
        payload = json.loads(out)
        assert payload["success"] is False
        assert payload["error_code"] == "INVOICE_ID_REQUIRED"

    def test_missing_employee_id_returns_identity_error(self, fake_client):
        out = invoice_verify_tool.invoke({
            "invoice_id": "INV-001",
            "employee_id": "",
            "trace_id": "t",
        })
        import json
        payload = json.loads(out)
        assert payload["success"] is False
        assert payload["error_code"] == "EMPLOYEE_ID_REQUIRED"


class TestClientErrorNormalization:
    """Client Adapter 自身：异常归一化为 4 类错误码。"""

    def _make_client(self):
        # 重新构造一个新的 McpEnterpriseOaClient 实例，使用 fake URL
        from app.integrations.mcp.enterprise_oa_client import McpEnterpriseOaClient
        return McpEnterpriseOaClient(
            url="http://127.0.0.1:1/mcp", timeout_seconds=0.1,
        )

    def test_unreachable_classified_correctly(self):
        from app.integrations.mcp.enterprise_oa_client import _classify_exception
        exc = ConnectionError("Connection refused")
        result = _classify_exception(exc)
        assert result.code == OA_MCP_UNREACHABLE

    def test_timeout_classified_correctly(self):
        from app.integrations.mcp.enterprise_oa_client import _classify_exception
        exc = TimeoutError("Read timeout")
        result = _classify_exception(exc)
        assert result.code == OA_MCP_TIMEOUT

    def test_validation_error_classified_as_invalid_response(self):
        from app.integrations.mcp.enterprise_oa_client import _classify_exception
        from pydantic import ValidationError
        try:
            ValidationError.from_exception_data("X", [])
        except Exception:
            pass
        # 直接构造一个名字含 Validation 的异常类
        class FakeValidation(Exception):
            pass
        exc = FakeValidation("parse failed")
        result = _classify_exception(exc)
        assert result.code == OA_MCP_INVALID_RESPONSE

    def test_unknown_exception_defaults_to_unreachable(self):
        from app.integrations.mcp.enterprise_oa_client import _classify_exception
        result = _classify_exception(RuntimeError("something weird"))
        assert result.code == OA_MCP_UNREACHABLE


class TestTransportRetryPolicy:
    """V2 §八：仅 OA_MCP_TIMEOUT / OA_MCP_UNREACHABLE 触发 1 次 transport retry。"""

    def test_timeout_triggers_retry(self):
        from app.integrations.mcp.enterprise_oa_client import McpEnterpriseOaClient
        client = McpEnterpriseOaClient(url="http://127.0.0.1:1/mcp", timeout_seconds=0.1)
        call_count = {"n": 0}

        def fake_call_once(tool_name, arguments):
            call_count["n"] += 1
            raise OaMcpClientError(OA_MCP_TIMEOUT, "timeout")

        with patch.object(client, "_call_once", side_effect=fake_call_once):
            result = client.travel_record_get(employee_id="E10001", limit=10)
        # 1 次初始 + 1 次 retry = 2 次
        assert call_count["n"] == 2
        assert result["success"] is False
        assert result["error_code"] == OA_MCP_TIMEOUT

    def test_tool_error_does_not_retry(self):
        from app.integrations.mcp.enterprise_oa_client import McpEnterpriseOaClient
        client = McpEnterpriseOaClient(url="http://127.0.0.1:1/mcp", timeout_seconds=0.1)
        call_count = {"n": 0}

        def fake_call_once(tool_name, arguments):
            call_count["n"] += 1
            raise OaMcpClientError(OA_MCP_TOOL_ERROR, "ownership reject")

        with patch.object(client, "_call_once", side_effect=fake_call_once):
            result = client.invoice_verify(
                invoice_id="INV-001", employee_id="E10001"
            )
        # 业务错误不重试，只调用 1 次
        assert call_count["n"] == 1
        assert result["success"] is False
        assert result["error_code"] == OA_MCP_TOOL_ERROR

    def test_invalid_response_does_not_retry(self):
        from app.integrations.mcp.enterprise_oa_client import McpEnterpriseOaClient
        client = McpEnterpriseOaClient(url="http://127.0.0.1:1/mcp", timeout_seconds=0.1)
        call_count = {"n": 0}

        def fake_call_once(tool_name, arguments):
            call_count["n"] += 1
            raise OaMcpClientError(OA_MCP_INVALID_RESPONSE, "bad json")

        with patch.object(client, "_call_once", side_effect=fake_call_once):
            result = client.travel_record_get(employee_id="E10001", limit=10)
        assert call_count["n"] == 1
        assert result["error_code"] == OA_MCP_INVALID_RESPONSE
