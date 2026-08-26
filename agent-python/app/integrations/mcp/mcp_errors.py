"""mcp_errors.py —— MCP Client Adapter 错误归一化

V2 §八：
- OA_MCP_TIMEOUT：连接 / 读取 / MCP 协议超时
- OA_MCP_UNREACHABLE：连接失败 / 连接被重置
- OA_MCP_INVALID_RESPONSE：响应结构不符合 MCP 协议或缺字段
- OA_MCP_TOOL_ERROR：MCP Tool 业务层返回 error（is_error=True）

Client 不重试 OA_MCP_TOOL_ERROR（业务层错误是确定性的，重试无意义）；
OA_MCP_TIMEOUT / OA_MCP_UNREACHABLE 由调用方决定 retry policy（本项目
V2 §八 限定 1 次 transport retry）。
"""

from __future__ import annotations


class OaMcpClientError(Exception):
    """Enterprise OA MCP Client 归一化错误。

    code 取值见模块顶部 4 个常量；message 为稳定中文文案（不带原始异常细节，
    防止内部堆栈泄露到 Planner / User）。
    """

    def __init__(self, code: str, message: str):
        super().__init__(f'{code}: {message}')
        self.code = code
        self.message = message


# 错误码常量（与 MCP server 的 ERR_* 错开，避免耦合）
OA_MCP_TIMEOUT = 'OA_MCP_TIMEOUT'
OA_MCP_UNREACHABLE = 'OA_MCP_UNREACHABLE'
OA_MCP_INVALID_RESPONSE = 'OA_MCP_INVALID_RESPONSE'
OA_MCP_TOOL_ERROR = 'OA_MCP_TOOL_ERROR'
