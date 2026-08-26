"""enterprise_tools.py —— 企业 Tool 实现

leave_balance_tool / leave_request_tool 为只读 Tool;leave_proposal_tool
复用受控业务动作链路(plan_annual_leave_action)生成待确认的申请草稿,
不会提交任何写操作。
仅供 Tool Executor 调用。Planner 看到的 arguments 不允许携带 employee_id /
trace_id 等系统字段,这些字段统一由 Executor 从 AgentState 注入。
所有结果都通过 json.dumps 返回结构化字符串,与 rag_answer_tool / eval_report_tool
风格一致。
"""

import json
from datetime import date
from typing import Any

from langchain_core.tools import tool

from app.clients.java_client import JavaClientError, get_java_client
from app.integrations.mcp.enterprise_oa_client import (
    OaMcpClientError,
    get_enterprise_oa_client,
)


def _json_default(obj: Any) -> Any:
    """企业 Tool 输出边界序列化:date 等非 JSON 原生类型在该边界写为 ISO 字符串。

    与 Tool Executor 端的反序列化语义对齐:
      - Tool _payload 输出 (JSON 字符串,含 ISO date)
      - Executor json.loads 还原 dict,并对 ISO date 字段手动 fromisoformat 还原 date 对象
      - 下游 (包括 AnnualLeaveActionProposal.strict=True schema) 拿到的是 Python date
    这样保持内部 Python 对象类型稳定,只在 HTTP 工具输出边界做一次确定性序列化。
    """
    if isinstance(obj, date):
        return obj.isoformat()
    raise TypeError(f'企业 Tool 输出边界不支持序列化类型: {type(obj).__name__}')


def _payload(success: bool, data: dict[str, Any] | None, error_code: str | None,
             message: str | None) -> str:
    body: dict[str, Any] = {'success': success}
    if data is not None:
        body.update(data)
    if error_code:
        body['error_code'] = error_code
    if message:
        body['message'] = message
    return json.dumps(body, ensure_ascii=False, default=_json_default)


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


@tool
def leave_proposal_tool(
    question: str = '',
    business_date: str = '',
    trace_id: str = '',
) -> str:
    """生成年假申请草稿(Proposal)供用户确认;不提交任何写操作。

    该 Tool 无 LLM 入参;question / business_date / trace_id 由 Tool Executor
    从 AgentState 注入,模型不得在 arguments 中提供这些字段,也不得提供
    employee_id / start_date / end_date / reason / half_day 等业务参数。
    返回 JSON 字符串:proposal 时携带 action_proposal 与 missing_fields=[];
    clarification 时 action_proposal 为 null 并携带 missing_fields。
    """
    from datetime import date

    from app.services import tool_calling_service

    if not question:
        return _payload(False, None, 'QUESTION_REQUIRED', '缺少原始问题，无法生成申请草稿。')
    if not business_date:
        return _payload(False, None, 'BUSINESS_DATE_REQUIRED', '当前业务日期不可用。')

    result = tool_calling_service.plan_annual_leave_action(
        question,
        business_date=date.fromisoformat(business_date),
        trace_id=trace_id,
    )
    if result.kind == 'proposal':
        return _payload(
            True,
            {
                'kind': 'proposal',
                # 内部 Python 对象保持原类型(start_date/end_date 为 date);
                # ISO 序列化只在 _payload 的 JSON 输出边界发生,
                # 下游 Tool Executor 会在解析端把 ISO 字符串还原回 date 对象,
                # 满足 AnnualLeaveActionProposal.strict=True schema 的类型契约。
                'action_proposal': result.proposal.model_dump(),
                'missing_fields': [],
                'message': '已生成年假申请草稿，请确认后提交。',
            },
            None,
            None,
        )
    if result.kind == 'clarification':
        return _payload(
            True,
            {
                'kind': 'clarification',
                'action_proposal': None,
                'missing_fields': result.clarification.missing_fields,
                'message': result.clarification.question,
            },
            None,
            None,
        )
    return _payload(
        False,
        None,
        result.error_code,
        '暂时无法生成申请草稿，请检查信息后重试。',
    )


# ──────────────────────────────────────────────────────────────────────
# P2-A Expense Workflow V1: travel_record_tool / invoice_verify_tool
# （Phase 3；Phase 7 加 expense_proposal_tool；Phase 8 加 expense_status_tool）
# ──────────────────────────────────────────────────────────────────────
#
# 这两个 Tool 都通过 Enterprise OA MCP Client Adapter 调用：
# - Planner 看到的是 Tool 业务接口（employee_id / invoice_id），看不到
#   transport / session / JSON-RPC / method names（V2 §八 / §二十二）。
# - identity_required=true：employee_id / trace_id 由 Executor 从 AgentState
#   注入；模型在 arguments 中**禁止**提供 employee_id（V2 §十一）。
# - travel_record_tool：LLM 无入参；employee_id 由 Executor 注入。
# - invoice_verify_tool：LLM 仅允许传 invoice_id；employee_id 由 Executor
#   注入；MCP 端再做 ownership check（V2 §七 + §十一）。
# ──────────────────────────────────────────────────────────────────────


@tool
def travel_record_tool(
    employee_id: str = '',
    trace_id: str = '',
    limit: int = 10,
) -> str:
    """查询当前登录用户自己的出差记录。

    该 Tool 无 LLM 入参；employee_id / trace_id / limit 全部由 Tool Executor
    从 AgentState 注入（V2 §十一）。模型不得在 arguments 中提供这些字段。

    返回 JSON 字符串：success 时携带 items（trip 列表，每条带关联
    expense_documents = invoice reference，仅作参考、需 invoice_verify 验真）。
    """
    eid = _require_identity(employee_id)
    if eid is None:
        return _identity_error()

    try:
        data = get_enterprise_oa_client().travel_record_get(
            employee_id=eid,
            limit=limit,
        )
    except OaMcpClientError as exc:
        return _payload(False, None, exc.code, str(exc))

    if not data.get('success', False):
        return _payload(
            False,
            None,
            data.get('error_code', 'OA_MCP_TOOL_ERROR'),
            data.get('message', 'MCP Tool 返回错误'),
        )
    items = data.get('items', [])
    return _payload(
        True,
        {
            'total': len(items),
            'items': items,
            'source': 'mcp:enterprise_oa',
        },
        None,
        None,
    )


@tool
def invoice_verify_tool(
    invoice_id: str = '',
    employee_id: str = '',
    trace_id: str = '',
) -> str:
    """校验发票 / 费用凭证。

    LLM 入参：invoice_id（V2 §十一：强制 identity_required=true，employee_id
    不得由 LLM 提供）。
    系统字段（由 Executor 注入）：employee_id / trace_id。

    返回 JSON 字符串：success 时携带 valid / amount / category / duplicate 等；
    跨员工调用由 MCP 端 ownership check 拒绝（OA_MCP_INVOICE_OWNERSHIP）。
    """
    eid = _require_identity(employee_id)
    if eid is None:
        return _identity_error()
    if not invoice_id or not invoice_id.strip():
        return _payload(
            False, None, 'INVOICE_ID_REQUIRED', '缺少 invoice_id 参数，无法验真。'
        )

    try:
        data = get_enterprise_oa_client().invoice_verify(
            invoice_id=invoice_id.strip(),
            employee_id=eid,
        )
    except OaMcpClientError as exc:
        return _payload(False, None, exc.code, str(exc))

    if not data.get('success', False):
        return _payload(
            False,
            None,
            data.get('error_code', 'OA_MCP_TOOL_ERROR'),
            data.get('message', 'MCP Tool 返回错误'),
        )
    return _payload(
        True,
        {
            'invoice_id': data.get('invoice_id'),
            'valid': data.get('valid'),
            'amount': data.get('amount'),
            'category': data.get('category'),
            'duplicate': data.get('duplicate'),
            'issued_at': data.get('issued_at'),
            'vendor': data.get('vendor'),
            'source': 'mcp:enterprise_oa',
        },
        None,
        None,
    )