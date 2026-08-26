"""enterprise_oa_client.py —— Enterprise OA MCP Client Adapter

V2 §八 + V2 §五 + V2 §六约束：
- Python Agent 工具只看到 success/data 或 success:false / error_code / message
  的归一化结构，看不到 transport / session / JSON-RPC / method names。
- 错误归一化：OA_MCP_TIMEOUT / OA_MCP_UNREACHABLE / OA_MCP_INVALID_RESPONSE /
  OA_MCP_TOOL_ERROR。
- 仅对当前 travel_record_get / invoice_verify 两个 side_effect=NONE /
  replay_safe=true 的 capability 允许 1 次 transport retry；不要把"所有 MCP
  Tool 默认 retry"做成全局规则——未来写副作用 Tool 时必须单独定义。
- Planner prompt 不出现 MCP / transport / session 等字样（本模块也遵守）。
- 不直接基于 ClientSession 构建 Adapter；优先使用 SDK 高层 Client(url=...)；
  仅当确需自定义 HTTP timeout/header/auth 时才显式构造 transport。
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Protocol

from .mcp_errors import (
    OA_MCP_INVALID_RESPONSE,
    OA_MCP_TIMEOUT,
    OA_MCP_TOOL_ERROR,
    OA_MCP_UNREACHABLE,
    OaMcpClientError,
)

logger = logging.getLogger(__name__)

DEFAULT_URL = "http://127.0.0.1:8100/mcp"
DEFAULT_TIMEOUT_SECONDS = 5.0
MAX_TRANSPORT_RETRIES = 1  # V2 §八：仅 1 次 transport retry


# ---------------------------------------------------------------------------
# 协议层（不依赖 MCP SDK）
# ---------------------------------------------------------------------------

class EnterpriseOaClient(Protocol):
    """Enterprise OA MCP Client 协议（不绑定具体 SDK 实现）。

    Tool / 单测通过此协议注入：测试用 InMemoryEnterpriseOaClient，生产用
    McpEnterpriseOaClient。
    """

    def travel_record_get(
        self, *, employee_id: str, limit: int = 10
    ) -> dict[str, Any]: ...

    def invoice_verify(
        self, *, invoice_id: str, employee_id: str
    ) -> dict[str, Any]: ...


# ---------------------------------------------------------------------------
# 单例工厂
# ---------------------------------------------------------------------------

_client_singleton: EnterpriseOaClient | None = None


def _build_default_url() -> str:
    return os.environ.get("ENTERPRISE_OA_MCP_URL", DEFAULT_URL)


def get_enterprise_oa_client() -> EnterpriseOaClient:
    """获取（或懒构造）Client 单例。

    单测可通过 monkeypatch `app.integrations.mcp.enterprise_oa_client._client_singleton`
    或 patch `get_enterprise_oa_client` 注入 fake client。
    """
    global _client_singleton
    if _client_singleton is None:
        url = _build_default_url()
        _client_singleton = McpEnterpriseOaClient(
            url=url, timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
        )
    return _client_singleton


def reset_enterprise_oa_client() -> None:
    """测试用：清空单例，下次 get_enterprise_oa_client() 会重新构造。"""
    global _client_singleton
    _client_singleton = None


# ---------------------------------------------------------------------------
# 真实 MCP SDK Client 实现（高层 Client API；按需显式构造 streamable_http transport）
# ---------------------------------------------------------------------------

class McpEnterpriseOaClient:
    """通过官方 MCP Python SDK v2 的高层 streamable_http Client 调用 server。

    设计：
    - 每次调用 _call 走 asyncio.run 包裹的短连接（简单且无状态；
      生产可改为长连接 + session 复用）。
    - 仅 1 次 transport retry（MAX_TRANSPORT_RETRIES=1）。
    - Tool 业务层错误（is_error=True）不重试；只对 OA_MCP_TIMEOUT /
      OA_MCP_UNREACHABLE 重试。
    """

    def __init__(self, *, url: str, timeout_seconds: float):
        self._url = url
        self._timeout_seconds = timeout_seconds

    # ---- 公共入口：tool 看不到 transport 细节 ----

    def travel_record_get(self, *, employee_id: str, limit: int = 10) -> dict[str, Any]:
        if not employee_id or not employee_id.strip():
            return _tool_error_payload(
                OA_MCP_INVALID_RESPONSE, "employee_id 不能为空"
            )
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            return _tool_error_payload(OA_MCP_INVALID_RESPONSE, "limit 必须是正整数")
        return self._call(
            "travel_record_get",
            {"employee_id": employee_id.strip(), "limit": limit},
        )

    def invoice_verify(self, *, invoice_id: str, employee_id: str) -> dict[str, Any]:
        if not invoice_id or not invoice_id.strip():
            return _tool_error_payload(
                OA_MCP_INVALID_RESPONSE, "invoice_id 不能为空"
            )
        if not employee_id or not employee_id.strip():
            return _tool_error_payload(
                OA_MCP_INVALID_RESPONSE, "employee_id 不能为空"
            )
        return self._call(
            "invoice_verify",
            {"invoice_id": invoice_id.strip(), "employee_id": employee_id.strip()},
        )

    # ---- 内部：MCP SDK v2 调用 + 错误归一化 + 1 次 transport retry ----

    def _call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        last_error: dict[str, Any] | None = None
        for attempt in range(MAX_TRANSPORT_RETRIES + 1):
            try:
                return self._call_once(tool_name, arguments)
            except OaMcpClientError as exc:
                # Tool 业务错误不重试
                if exc.code == OA_MCP_TOOL_ERROR:
                    return _tool_error_payload(exc.code, exc.message)
                # 仅 timeout / unreachable 触发 transport retry
                if exc.code not in (OA_MCP_TIMEOUT, OA_MCP_UNREACHABLE):
                    return _tool_error_payload(exc.code, exc.message)
                last_error = _tool_error_payload(exc.code, exc.message)
                logger.warning(
                    "MCP client transport failure tool=%s attempt=%d code=%s",
                    tool_name, attempt + 1, exc.code,
                )
        assert last_error is not None
        return last_error

    def _call_once(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """通过 MCP SDK v2 Client(url=...) 高层 API 调用一次 tool。

        SDK v2 的 Client 高层 API：构造传入 server=url，async with 内部完成
        initialize / list_tools 等握手；call_tool 直接 await。
        """
        try:
            from mcp import Client  # SDK v2 high-level Client
        except ImportError as exc:
            raise OaMcpClientError(
                OA_MCP_UNREACHABLE,
                "MCP SDK 未安装或不可用",
            ) from exc

        async def _invoke() -> dict[str, Any]:
            client = Client(self._url, read_timeout_seconds=self._timeout_seconds)
            try:
                async with client:
                    result = await client.call_tool(tool_name, arguments)
            except Exception as exc:
                raise _classify_exception(exc) from exc
            return _normalize_result(result)

        try:
            return asyncio.run(_invoke())
        except OaMcpClientError:
            raise
        except Exception as exc:
            # asyncio.run 自身 / 其它未捕获
            raise OaMcpClientError(
                OA_MCP_UNREACHABLE, "MCP client 调用未完成"
            ) from exc


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------

def _normalize_result(result: Any) -> dict[str, Any]:
    """把 MCP CallToolResult 转成 {success, ...data} 或抛 OaMcpClientError。"""
    is_error = getattr(result, "is_error", False)
    content = getattr(result, "content", None) or []
    if not content:
        raise OaMcpClientError(
            OA_MCP_INVALID_RESPONSE, "MCP 响应 content 为空"
        )
    import json

    first = content[0]
    text = getattr(first, "text", None)
    if not isinstance(text, str):
        raise OaMcpClientError(
            OA_MCP_INVALID_RESPONSE, "MCP 响应 content[0].text 不是字符串"
        )
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise OaMcpClientError(
            OA_MCP_INVALID_RESPONSE, "MCP 响应不是合法 JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise OaMcpClientError(
            OA_MCP_INVALID_RESPONSE, "MCP 响应不是 JSON 对象"
        )
    if is_error:
        # Tool 业务层错误（V2 §八 OA_MCP_TOOL_ERROR），不重试
        code = payload.get("error_code") or OA_MCP_TOOL_ERROR
        message = payload.get("message") or "MCP Tool 返回错误"
        raise OaMcpClientError(code, message)
    return payload


def _classify_exception(exc: Exception) -> OaMcpClientError:
    """MCP SDK / 网络异常 → 4 类错误码之一。"""
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    if "timeout" in name or "timeout" in msg:
        return OaMcpClientError(OA_MCP_TIMEOUT, "MCP 调用超时")
    if (
        "connect" in name
        or "connection" in msg
        or "refused" in msg
        or "reset" in msg
        or "unreachable" in msg
        or "httpxconnect" in name
    ):
        return OaMcpClientError(OA_MCP_UNREACHABLE, "MCP 服务不可达")
    if "validation" in name or "parse" in msg:
        return OaMcpClientError(OA_MCP_INVALID_RESPONSE, "MCP 响应格式异常")
    # 未识别：归 UNREACHABLE（保守）；上层可按 code 区分
    return OaMcpClientError(OA_MCP_UNREACHABLE, "MCP 调用失败")


def _tool_error_payload(code: str, message: str) -> dict[str, Any]:
    """Tool 视角的统一 error 字典（success=False）。"""
    return {
        "success": False,
        "error_code": code,
        "message": message,
    }
