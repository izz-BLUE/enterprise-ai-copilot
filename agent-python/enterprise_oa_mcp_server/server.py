"""server.py —— Enterprise OA MCP Server 入口

V2 §六 + §七：
- 使用官方 MCP Python SDK v2.1.1（mcp.server.MCPServer）
- transport: Streamable HTTP（V2 §六 默认 http://127.0.0.1:8100/mcp）
- 配置：ENTERPRISE_OA_MCP_HOST / ENTERPRISE_OA_MCP_PORT / ENTERPRISE_OA_MCP_PATH
  （ENTERPRISE_OA_MCP_URL 由 client 侧构建，无需 server 关心）

注意：本 server 不引入 DB / Docker / OAuth / 服务发现；
仅 in-memory fixtures + asyncio run。
"""

from __future__ import annotations

import logging
import os

from mcp.server import MCPServer

from .tools import register_tools

logger = logging.getLogger("enterprise_oa_mcp_server")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8100
DEFAULT_PATH = "/mcp"


def build_server() -> MCPServer:
    """构造 MCPServer 实例；可被测试 in-process 调用或被 __main__ 启动。"""
    server = MCPServer(
        name="enterprise-oa-mcp",
        title="Enterprise OA MCP Server",
        description=(
            "Enterprise OA 内部 MCP 服务（P2-A Expense Workflow V1），"
            "提供 travel_record_get / invoice_verify 两个 capability。"
        ),
        instructions=(
            "两个 capability：travel_record_get(employee_id, limit) 与 "
            "invoice_verify(invoice_id, employee_id)。employee_id 是 trusted "
            "system field，调用方必须从可信链路注入；invoice_verify 在 MCP "
            "端做 ownership check。"
        ),
    )
    register_tools(server)
    return server


def read_host_port_path() -> tuple[str, int, str]:
    host = os.environ.get("ENTERPRISE_OA_MCP_HOST", DEFAULT_HOST)
    port_str = os.environ.get("ENTERPRISE_OA_MCP_PORT", str(DEFAULT_PORT))
    path = os.environ.get("ENTERPRISE_OA_MCP_PATH", DEFAULT_PATH)
    try:
        port = int(port_str)
    except ValueError:
        logger.warning(
            "ENTERPRISE_OA_MCP_PORT=%r 非整数，回退到 %d",
            port_str,
            DEFAULT_PORT,
        )
        port = DEFAULT_PORT
    return host, port, path
