"""enterprise_tools.py —— 只读企业 Tool 实现

仅供 Tool Executor 调用。Planner 看到的 arguments 不允许携带 employee_id /
trace_id,这些字段统一由 Executor 从 AgentState 注入。
所有结果都通过 json.dumps 返回结构化字符串,与 rag_answer_tool / eval_report_tool
风格一致。
"""

import json
from typing import Any

from langchain_core.tools import tool

from app.clients.java_client import JavaClientError, get_java_client


def _payload(success: bool, data: dict[str, Any] | None, error_code: str | None,
             message: str | None) -> str:
    body: dict[str, Any] = {'success': success}
    if data is not None:
        body.update(data)
    if error_code:
        body['error_code'] = error_code
    if message:
        body['message'] = message
    return json.dumps(body, ensure_ascii=False)


def _require_identity(employee_id: str) -> str | None:
    """缺身份时返回稳定错误 payload;否则原样返回 employee_id。"""
    if not employee_id:
        return None
    return employee_id


def _identity_error() -> str:
    return _payload(False, None, 'EMPLOYEE_ID_REQUIRED',
                    '当前请求缺少员工身份，请联系管理员。')


@tool
def leave_balance_tool(
    employee_id: str = '',
    trace_id: str = '',
) -> str:
    """查询当前登录用户自己的年假余额。

    该 Tool 无 LLM 入参;employee_id / trace_id 由 Tool Executor 从 AgentState 注入,
    模型不得在 arguments 中提供这些字段。
    """
    eid = _require_identity(employee_id)
    if eid is None:
        return _identity_error()

    try:
        data = get_java_client().get_leave_balance(
            employee_id=eid,
            trace_id=trace_id,
        )
    except JavaClientError as exc:
        return _payload(False, None, exc.code, str(exc))

    return _payload(
        True,
        {
            'annual_balance': data.get('annualBalance'),
            'updated_at': data.get('updatedAt'),
            'source': 'java',
        },
        None,
        None,
    )


@tool
def leave_request_tool(
    limit: int = 20,
    employee_id: str = '',
    trace_id: str = '',
) -> str:
    """查询当前登录用户自己已成功提交的最近请假记录(按 submitted_at 倒序)。

    LLM 入参:
      limit  - 1..50 的整数,默认 20。
    系统字段(由 Executor 注入):employee_id / trace_id。
    暂不支持按 pending/cancelled 过滤:leave_request 表当前只持久化已成功执行的请求,
    PendingAction 状态由 business_action 表维护,本 Tool 不暴露。
    """
    eid = _require_identity(employee_id)
    if eid is None:
        return _identity_error()

    try:
        data = get_java_client().list_leave_requests(
            employee_id=eid,
            trace_id=trace_id,
            limit=limit,
        )
    except JavaClientError as exc:
        return _payload(False, None, exc.code, str(exc))

    items = data.get('items', [])
    return _payload(
        True,
        {
            'total': data.get('total', len(items)),
            'items': items,
            'source': 'java',
        },
        None,
        None,
    )