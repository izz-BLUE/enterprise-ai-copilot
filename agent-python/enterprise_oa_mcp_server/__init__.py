"""enterprise_oa_mcp_server —— Enterprise OA MCP Server

P2-A Expense Workflow V1 的极简 MCP 服务：
- 仅两个 capability：travel_record_get、invoice_verify
- transport：Streamable HTTP（V2 §六，默认 http://127.0.0.1:8100/mcp）
- 内部 in-memory fixtures（V2 §七），不引入 DB / Docker / OAuth / 服务发现
- 错误统一为 {success:false, error_code, message}
- invoice_verify 在 MCP 端做 ownership check（employee_id 必须匹配 owner_employee_id）

启动方式：
    cd agent-python
    uv run --project enterprise_oa_mcp_server python -m enterprise_oa_mcp_server
或先 sync：
    cd agent-python/enterprise_oa_mcp_server
    uv sync
    uv run python -m enterprise_oa_mcp_server
"""
