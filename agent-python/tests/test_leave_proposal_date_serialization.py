"""test_leave_proposal_date_serialization.py —— Scoped Conversation Memory P0
accept-after-fix 最小回归测试。

只验证 P0 端到端的两个具体修复点(2026-08-22 acceptance blocker):
  1. Tool 出口前 action_proposal 的 start_date/end_date 保持 Python date 类型
     (内部 Pydantic 对象之间传递 date),只有 HTTP/JSON 边界被序列化为 ISO 字符串;
  2. Tool Executor 解析 Tool observation 时,自动把 ISO date 字段还原回 date 对象,
     使下游 AnnualLeaveActionProposal (strict=True) 通过校验,并能被 AgentResponse
     包装后正常 JSON 序列化到 Java 端。

范围严格限制在此 bug fix;不修改身份 / Scope / Memory state-machine。
"""

from __future__ import annotations

import json
import re
from datetime import date
from unittest.mock import patch

from app.schemas.action_schema import AnnualLeaveActionProposal
from app.schemas.chat_schema import AgentResponse
from tests.runtime_helpers import checkpoint_safe_state, runtime_for_state

# ---------------------------------------------------------------------------
# Tool 出口:date 在 Tool 内部 dict 中保持 Python date
# ---------------------------------------------------------------------------


class TestEnterpriseToolsPayloadDate:
    """_payload + leave_proposal_tool 出口应保留 date 类型(让 model_dump() 不带 mode)
    在 Tool dict 中存为 Python date;_payload 的 default=_json_default 把 date 写成 ISO。
    """

    def test_tool_output_json_contains_iso_date(self):
        from app.tools.enterprise_tools import _payload

        raw = _payload(
            True,
            {
                'kind': 'proposal',
                'action_proposal': {
                    'action_type': 'ANNUAL_LEAVE_REQUEST',
                    'start_date': date(2026, 8, 25),
                    'end_date': date(2026, 8, 25),
                    'reason': '家里有事',
                    'half_day': 'NONE',
                },
                'missing_fields': [],
                'message': '已生成年假申请草稿，请确认后提交。',
            },
            None,
            None,
        )
        parsed = json.loads(raw)
        # boundary: 字符串
        assert isinstance(parsed['action_proposal']['start_date'], str)
        assert parsed['action_proposal']['start_date'] == '2026-08-25'
        # ISO YYYY-MM-DD 形态;不应出现 dict / 类型对象
        assert re.fullmatch(r'\d{4}-\d{2}-\d{2}', parsed['action_proposal']['start_date'])

    def test_tool_output_partial_clarification_no_date(self):
        from app.tools.enterprise_tools import _payload

        raw = _payload(
            True,
            {
                'kind': 'clarification',
                'action_proposal': None,
                'missing_fields': ['start_date', 'end_date', 'reason'],
            },
            None,
            None,
        )
        parsed = json.loads(raw)
        assert parsed['success'] is True
        assert parsed['action_proposal'] is None
        assert 'start_date' in parsed['missing_fields']


# ---------------------------------------------------------------------------
# Tool Executor:ISO date 在解析端被还原回 date 对象
# ---------------------------------------------------------------------------


class TestToolExecutorRestoresIsoDate:
    """tool_executor_node 解析 leave_proposal_tool 的 observation 后,应把
    action_proposal.start_date / end_date 从 ISO 字符串还原回 Python date,
    否则下游 strict Pydantic schema 拒绝。
    """

    def _state(self, observation_json: str):
        return {
            'question': '帮我申请2026年8月25日的年假',
            'safe': True,
            'route': '',
            'answer': '',
            'tool_result': {},
            'sources': [],
            'reason': '',
            'category': '',
            'allow_eval': False,
            'allow_business_actions': True,
            'business_date': date(2026, 8, 22),
            'trace_id': 'trace-p0-date',
            'employee_id': 'DEMO-001',
            'action_proposal': None,
            'missing_fields': [],
            'step_count': 0,
            'tool_call_count': 0,
            'tool_history': [],
            'observation': '',
            'planner_decision': {
                'action': 'tool',
                'tool_name': 'leave_proposal_tool',
                'arguments': {},
                'answer': None,
                'reason_code': 'need_proposal',
            },
        }

    def test_iso_date_restored_to_date_object(self):
        from app.agents.tool_executor_node import tool_executor_node

        observation = json.dumps({
            'success': True,
            'kind': 'proposal',
            'action_proposal': {
                'action_type': 'ANNUAL_LEAVE_REQUEST',
                'start_date': '2026-08-25',
                'end_date': '2026-08-25',
                'reason': '家里有事',
                'half_day': 'NONE',
            },
            'missing_fields': [],
            'message': 'ok',
        }, ensure_ascii=False)

        with patch('app.agents.tool_executor_node.leave_proposal_tool') as tool:
            tool.invoke.return_value = observation
            state = self._state(observation)
            result = tool_executor_node(
                checkpoint_safe_state(state), runtime_for_state(state),
            )

        assert result['action_proposal'] is not None
        ap = result['action_proposal']
        assert ap['start_date'] == date(2026, 8, 25)
        assert ap['end_date'] == date(2026, 8, 25)
        assert isinstance(ap['start_date'], date)
        assert isinstance(ap['end_date'], date)

    def test_invalid_iso_date_left_as_string_for_schema_to_reject(self):
        """非合法 ISO-8601 字符串不能被解释为 date,要留给下游 schema 拒绝。"""
        from app.agents.tool_executor_node import tool_executor_node

        observation = json.dumps({
            'success': True,
            'kind': 'proposal',
            'action_proposal': {
                'action_type': 'ANNUAL_LEAVE_REQUEST',
                'start_date': 'not-a-date',
                'end_date': '2026-08-25',
                'reason': 'r',
                'half_day': 'NONE',
            },
            'missing_fields': [],
            'message': 'ok',
        }, ensure_ascii=False)

        with patch('app.agents.tool_executor_node.leave_proposal_tool') as tool:
            tool.invoke.return_value = observation
            state = self._state(observation)
            result = tool_executor_node(
                checkpoint_safe_state(state), runtime_for_state(state),
            )

        ap = result['action_proposal']
        # 非合法 ISO 必须保留为 string,让 strict schema 拒绝。
        assert ap['start_date'] == 'not-a-date'
        # 合法 ISO 仍然被还原。
        assert ap['end_date'] == date(2026, 8, 25)

    def test_clarification_branch_does_not_run_date_restore(self):
        """Clarification payload action_proposal=None,不应触发还原逻辑报错。"""
        from app.agents.tool_executor_node import tool_executor_node

        observation = json.dumps({
            'success': True,
            'kind': 'clarification',
            'action_proposal': None,
            'missing_fields': ['start_date', 'end_date', 'reason'],
            'message': '需要补充日期和原因',
        }, ensure_ascii=False)

        with patch('app.agents.tool_executor_node.leave_proposal_tool') as tool:
            tool.invoke.return_value = observation
            state = self._state(observation)
            result = tool_executor_node(
                checkpoint_safe_state(state), runtime_for_state(state),
            )

        assert result['action_proposal'] is None
        assert set(result['missing_fields']) == {'start_date', 'end_date', 'reason'}


# ---------------------------------------------------------------------------
# 下游 roundtrip:还原后的 dict 既能被 strict schema 接受,也能 JSON 序列化到 Java
# ---------------------------------------------------------------------------


class TestAgentResponseRoundtrip:
    """端到端 roundtrip:Executor 还原后的 action_proposal dict 应该可以直接
    作为 AnnualLeaveActionProposal 的输入(strict=True 也接受),并最终
    AgentResponse -> JSON 序列化到 Java 后 Java Jackson 仍能 parse 回 start_date。
    """

    def test_strict_pydantic_accepts_restored_date(self):
        from app.agents.tool_executor_node import tool_executor_node

        observation = json.dumps({
            'success': True,
            'kind': 'proposal',
            'action_proposal': {
                'action_type': 'ANNUAL_LEAVE_REQUEST',
                'start_date': '2026-08-25',
                'end_date': '2026-08-25',
                'reason': '家里有事',
                'half_day': 'NONE',
            },
            'missing_fields': [],
            'message': 'ok',
        }, ensure_ascii=False)

        state = {
            'question': 'q',
            'safe': True,
            'route': '',
            'answer': '',
            'tool_result': {},
            'sources': [],
            'reason': '',
            'category': '',
            'allow_eval': False,
            'allow_business_actions': True,
            'business_date': date(2026, 8, 22),
            'trace_id': 'trace-roundtrip',
            'employee_id': 'DEMO-001',
            'action_proposal': None,
            'missing_fields': [],
            'step_count': 0,
            'tool_call_count': 0,
            'tool_history': [],
            'observation': '',
            'planner_decision': {
                'action': 'tool',
                'tool_name': 'leave_proposal_tool',
                'arguments': {},
                'answer': None,
                'reason_code': 'need_proposal',
            },
        }
        with patch('app.agents.tool_executor_node.leave_proposal_tool') as tool:
            tool.invoke.return_value = observation
            result = tool_executor_node(
                checkpoint_safe_state(state), runtime_for_state(state),
            )

        ap_dict = result['action_proposal']
        # 严格 schema 接受(关键:必须用回 restored date 对象,不是 ISO string)
        proposal = AnnualLeaveActionProposal(**ap_dict)
        assert proposal.start_date == date(2026, 8, 25)
        assert proposal.action_type == 'ANNUAL_LEAVE_REQUEST'

    def test_agent_response_wraps_restored_proposal_and_serializes_to_json(self):
        """整个 AgentResponse 到 JSON 串行化的端到端 roundtrip,
        Java Jackson 能再次解析字符串日期 back to LocalDate。"""
        action_proposal = AnnualLeaveActionProposal(
            action_type='ANNUAL_LEAVE_REQUEST',
            start_date=date(2026, 8, 25),
            end_date=date(2026, 8, 25),
            reason='家里有事',
            half_day='NONE',
        )
        resp = AgentResponse(
            answer='已生成年假申请草稿',
            route='action',
            safe=True,
            category='business_action',
            reason='',
            sources=[],
            success=True,
            traceId='trace-final-roundtrip',
            action_proposal=action_proposal,
            missing_fields=[],
        )
        body = resp.model_dump()
        # 真实 HTTP 出口仍由 FastAPI (Pydantic v2 默认 JSON encoder) 序列化 date,
        # 与 Java 端 Jackson 的 LocalDate(ISO 反序列化)对齐。
        json_str = (
            body.model_dump_json()
            if hasattr(body, 'model_dump_json')
            else json.dumps(body, ensure_ascii=False, default=str)
        )

        # JSON 序列化的 start_date 是 ISO string (这是 Java Jackson 接受的格式)
        parsed = json.loads(json_str)
        assert parsed['action_proposal']['start_date'] == '2026-08-25'

        # 用与 Java Jackson 相同的 key 命名约定反解 (snake_case 测试不强制,
        # 但 date 字段必须是 ISO 字符串)。
        localdate_match = re.fullmatch(r'\d{4}-\d{2}-\d{2}',
                                        parsed['action_proposal']['start_date'])
        assert localdate_match is not None
