"""主 Agent 运行环境的 MCP SDK 依赖闭环 smoke test。"""

from __future__ import annotations

from importlib.metadata import version


def test_main_agent_runtime_imports_mcp_sdk_and_client_adapter():
    import mcp
    from mcp import Client

    from app.integrations.mcp.enterprise_oa_client import EnterpriseOaClient

    assert mcp is not None
    assert Client is not None
    assert version('mcp').startswith('2.')
    assert EnterpriseOaClient is not None
