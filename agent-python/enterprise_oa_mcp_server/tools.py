"""tools.py —— MCP Server 注册的两个 capability

V2 §七 + V2 §八：
- travel_record_get(employee_id, limit) → in-memory fixture 查询
- invoice_verify(invoice_id, employee_id) → in-memory fixture 查询 +
  ownership check（V2 §七 强制）

错误统一：
  {"success": False, "error_code": "...", "message": "..."}
成功：
  {"success": True, ...}

MCP SDK v2.1.1（mcp.server.MCPServer）：
- @server.tool(name=..., description=...) 注册
- 返回 mcp.types.CallToolResult，content 字段用 TextContent 包装 JSON 字符串
"""

from __future__ import annotations

import json

from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent

from . import fixtures

# 错误码常量（与 V2 §八 Client Adapter 错误归一化保持同名同语义）
ERR_INVALID_INPUT = "OA_MCP_INVALID_INPUT"
ERR_INVOICE_NOT_FOUND = "OA_MCP_INVOICE_NOT_FOUND"
ERR_INVOICE_OWNERSHIP = "OA_MCP_INVOICE_OWNERSHIP"
ERR_EMPLOYEE_REQUIRED = "OA_MCP_EMPLOYEE_REQUIRED"


def _success_payload(data: dict) -> CallToolResult:
    """统一 success 包装：data 通过 TextContent 返回 JSON 字符串。"""
    payload = {"success": True, **data}
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
        structured_content=payload,
    )


def _error_payload(error_code: str, message: str) -> CallToolResult:
    """统一 error 包装：is_error=True + content TextContent JSON。"""
    payload = {"success": False, "error_code": error_code, "message": message}
    return CallToolResult(
        is_error=True,
        content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
        structured_content=payload,
    )


def _serialize_trip(trip: dict) -> dict:
    """过滤 trip 内部细节，保持 MCP 出站契约稳定。"""
    return {
        "trip_id": trip["trip_id"],
        "employee_id": trip["employee_id"],
        "destination": trip["destination"],
        "start_date": trip["start_date"],
        "end_date": trip["end_date"],
        "purpose": trip["purpose"],
        "status": trip["status"],
        "expense_documents": [
            {
                "invoice_id": doc["invoice_id"],
                "category": doc["category"],
                "declared_amount": doc["declared_amount"],
                "description": doc["description"],
            }
            for doc in trip.get("expense_documents", [])
        ],
    }


def travel_record_get_impl(employee_id: str, limit: int = 10) -> CallToolResult:
    """travel_record_get 纯函数实现（不依赖 MCPServer 注册上下文）。

    与 register_tools 中 @server.tool 装饰器版本共享语义；单测可绕过
    MCP 协议层 Pydantic ValidationError 直接验证业务逻辑。
    """
    if not employee_id or not employee_id.strip():
        return _error_payload(ERR_EMPLOYEE_REQUIRED, "employee_id 不能为空")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        return _error_payload(ERR_INVALID_INPUT, "limit 必须是正整数")
    trips = fixtures.list_trips_for_employee(employee_id.strip())
    items = [_serialize_trip(t) for t in trips[:limit]]
    return _success_payload({"items": items})


def invoice_verify_impl(invoice_id: str, employee_id: str) -> CallToolResult:
    """invoice_verify 纯函数实现（同上：绕过 MCP 协议层供单测）。"""
    if not invoice_id or not invoice_id.strip():
        return _error_payload(ERR_INVALID_INPUT, "invoice_id 不能为空")
    if not employee_id or not employee_id.strip():
        return _error_payload(ERR_EMPLOYEE_REQUIRED, "employee_id 不能为空")
    invoice = fixtures.get_invoice(invoice_id.strip())
    if invoice is None:
        return _error_payload(
            ERR_INVOICE_NOT_FOUND, f"未找到 invoice_id={invoice_id} 的发票"
        )
    if invoice["owner_employee_id"] != employee_id.strip():
        return _error_payload(
            ERR_INVOICE_OWNERSHIP,
            f"invoice {invoice_id} 不属于 employee {employee_id}",
        )
    return _success_payload(
        {
            "invoice_id": invoice["invoice_id"],
            "valid": invoice["valid"],
            "amount": invoice["amount"],
            "category": invoice["category"],
            "duplicate": invoice["duplicate"],
            "issued_at": invoice["issued_at"],
            "vendor": invoice["vendor"],
            **(
                {"duplicate_of": invoice["duplicate_of"]}
                if invoice.get("duplicate")
                else {}
            ),
            **(
                {"invalid_reason": invoice["invalid_reason"]}
                if not invoice["valid"]
                else {}
            ),
        }
    )


def register_tools(server: MCPServer) -> None:
    """在传入的 MCPServer 上注册两个 capability。

    实现委托给 travel_record_get_impl / invoice_verify_impl，便于单测
    绕过 MCP 协议层 Pydantic ValidationError 直接验证业务逻辑。
    """

    @server.tool(
        name="travel_record_get",
        description=(
            "查询当前员工自己的出差记录。employee_id 必须由调用方提供（"
            "trusted system field，不接受 LLM 伪造）。返回按 trip_id 倒序"
            "的列表，每条 trip 携带关联 expense_documents（invoice_id / "
            "category / declared_amount），invoice reference 不代表验真，"
            "仍需经过 invoice_verify。"
        ),
    )
    def travel_record_tool_entry(employee_id: str, limit: int = 10) -> CallToolResult:
        return travel_record_get_impl(employee_id, limit)

    @server.tool(
        name="invoice_verify",
        description=(
            "校验发票 / 费用凭证。invoice_id + employee_id 必填；MCP 在端内"
            "做 ownership check（invoice.owner_employee_id == employee_id），"
            "跨员工调用返回 OA_MCP_INVOICE_OWNERSHIP。返回 valid / amount / "
            "category / duplicate 等字段；duplicate=true 表示已被报销过。"
        ),
    )
    def invoice_verify_tool_entry(invoice_id: str, employee_id: str) -> CallToolResult:
        return invoice_verify_impl(invoice_id, employee_id)
