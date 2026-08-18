"""test_leave_proposal_tool.py —— leave_proposal_tool 真实函数行为测试

工具函数真实执行，受控链路（plan_annual_leave_action）用 stub 隔离：
- proposal：返回 kind=proposal + action_proposal + missing_fields=[]
- clarification：返回 kind=clarification + action_proposal=None + missing_fields
- 缺 question / business_date：稳定错误 payload
- 不发起任何真实写操作（受控链路被 stub，未进入提交路径）
"""

import json
from datetime import date
from unittest.mock import patch

from app.schemas.action_schema import (
    AnnualLeaveActionProposal,
    AnnualLeaveClarification,
    ClarificationPlanningResult,
    ProposalPlanningResult,
)
from app.tools.enterprise_tools import leave_proposal_tool


def _proposal_result():
    return ProposalPlanningResult(proposal=AnnualLeaveActionProposal(
        action_type='ANNUAL_LEAVE_REQUEST',
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 5),
        reason='私事',
        half_day='NONE',
    ))


class TestLeaveProposalTool:
    def test_proposal_payload_structure(self):
        with patch('app.services.tool_calling_service.plan_annual_leave_action',
                   return_value=_proposal_result()) as planner:
            raw = leave_proposal_tool.invoke({
                'question': '申请2026-09-01到2026-09-05年假，原因为私事',
                'business_date': '2026-08-18',
                'trace_id': 'trace-tool',
            })
        planner.assert_called_once()
        payload = json.loads(raw)
        assert payload['success'] is True
        assert payload['kind'] == 'proposal'
        assert payload['missing_fields'] == []
        assert payload['action_proposal']['action_type'] == 'ANNUAL_LEAVE_REQUEST'
        assert payload['action_proposal']['start_date'] == '2026-09-01'
        assert payload['action_proposal']['end_date'] == '2026-09-05'
        assert payload['action_proposal']['reason'] == '私事'

    def test_clarification_payload_structure(self):
        clarification = ClarificationPlanningResult(
            clarification=AnnualLeaveClarification(
                missing_fields=['reason'],
                question='请补充申请原因。',
            )
        )
        with patch('app.services.tool_calling_service.plan_annual_leave_action',
                   return_value=clarification):
            raw = leave_proposal_tool.invoke({
                'question': '申请2026-09-01到2026-09-05年假',
                'business_date': '2026-08-18',
                'trace_id': 'trace-tool',
            })
        payload = json.loads(raw)
        assert payload['success'] is True
        assert payload['kind'] == 'clarification'
        assert payload['action_proposal'] is None
        assert payload['missing_fields'] == ['reason']

    def test_missing_question_returns_stable_error(self):
        raw = leave_proposal_tool.invoke({
            'question': '',
            'business_date': '2026-08-18',
            'trace_id': 'trace-tool',
        })
        payload = json.loads(raw)
        assert payload['success'] is False
        assert payload['error_code'] == 'QUESTION_REQUIRED'

    def test_missing_business_date_returns_stable_error(self):
        raw = leave_proposal_tool.invoke({
            'question': '申请2026-09-01一天年假，原因为私事',
            'business_date': '',
            'trace_id': 'trace-tool',
        })
        payload = json.loads(raw)
        assert payload['success'] is False
        assert payload['error_code'] == 'BUSINESS_DATE_REQUIRED'

    def test_no_write_operation_invoked(self):
        """受控链路被 stub 后不触发任何提交：plan_annual_leave_action 仅生成草稿。"""
        with patch('app.services.tool_calling_service.plan_annual_leave_action',
                   return_value=_proposal_result()) as planner, \
             patch('app.services.tool_calling_service._get_controlled_tool_client') as client:
            leave_proposal_tool.invoke({
                'question': '申请2026-09-01到2026-09-05年假，原因为私事',
                'business_date': '2026-08-18',
                'trace_id': 'trace-tool',
            })
        # 受控 client 未被触碰；写操作链路（确认/提交）完全未进入
        client.assert_not_called()
        planner.assert_called_once()
