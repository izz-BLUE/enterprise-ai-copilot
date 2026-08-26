"""test_server_streamable_http.py —— Streamable HTTP transport smoke

V2 §六：MCP server 通过 run_streamable_http_async() 在
http://127.0.0.1:8100/mcp 暴露 Streamable HTTP。本测试：

1. 启动 server（后台任务）
2. 用 MCP SDK v2 Client(url=...) 真实连入并 list_tools
3. 真实调用 travel_record_get 验证端到端契约
4. 关闭 server

不依赖真实网络 IO；MCP 客户端通过 SDK 的 in-process ClientSession 调用。
"""

from __future__ import annotations

import asyncio
import json

import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from enterprise_oa_mcp_server.server import build_server, read_host_port_path


async def _run_smoke():
    host, port, path = read_host_port_path()
    server = build_server()
    server_task = asyncio.create_task(
        server.run_streamable_http_async(host=host, port=port, streamable_http_path=path)
    )
    # 等待 server 启动
    await asyncio.sleep(1.5)

    url = f"http://{host}:{port}{path}"
    try:
        async with streamable_http_client(url) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                init = await session.initialize()
                assert init.capabilities, "MCP initialize 应返回非空 capabilities"

                tools_resp = await session.list_tools()
                tool_names = {t.name for t in tools_resp.tools}
                assert tool_names == {"travel_record_get", "invoice_verify"}

                # 端到端调用 travel_record_get
                call = await session.call_tool(
                    "travel_record_get",
                    {"employee_id": "E10001", "limit": 10},
                )
                assert not call.is_error
                payload = json.loads(call.content[0].text)
                assert payload["success"] is True
                assert len(payload["items"]) == 3

                # ownership check：跨员工 invoice
                bad = await session.call_tool(
                    "invoice_verify",
                    {"invoice_id": "INV-005", "employee_id": "E10001"},
                )
                assert bad.is_error
                bad_payload = json.loads(bad.content[0].text)
                assert bad_payload["error_code"] == "OA_MCP_INVOICE_OWNERSHIP"
    finally:
        server_task.cancel()
        try:
            await server_task
        except (asyncio.CancelledError, Exception):
            pass


def test_streamable_http_round_trip():
    asyncio.run(_run_smoke())
