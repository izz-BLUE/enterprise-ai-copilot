"""app.integrations.mcp —— MCP Client Adapter 层

隔离 MCP 协议细节（transport / session / JSON-RPC）到 Agent 业务层之外；
Planner prompt 与 Tool 实现只看到 success/error_code/message 的归一化结构。
"""
