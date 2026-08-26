"""test_composite_enterprise_proposal.py —— Composite Enterprise Task P0 核心场景

目标场景：用户用一句话表达"先查知识/余额，再准备年假申请"的复合任务。
Agent Loop 模式下 Planner-first，业务动作统一走 leave_proposal_tool：
- safety → planner ⇄ tool_executor
- leave_proposal_tool 由 Executor 注入原始问题 / business_date / trace_id，
  内部复用受控链路生成 Proposal 或 Clarification，并写回 AgentState
- 不经过 router_node，也没有 action_node 特殊出口
"""

import json
from contextlib import ExitStack
from datetime import date
from unittest.mock import Mock, patch

from app.agents.langgraph_agent import run_langgraph_agent
from app.agents.tool_executor_node import tool_executor_node as _tool_executor_node
from app.schemas.action_schema import (
    AnnualLeaveActionProposal,
    ProposalPlanningResult,
)
from tests.runtime_helpers import checkpoint_safe_state, runtime_for_state

BUSINESS_DATE = date(2026, 8, 18)


def tool_executor_node(value, runtime=None):
    if runtime is None:
        runtime = runtime_for_state(value)
        value = checkpoint_safe_state(value)
    return _tool_executor_node(value, runtime)


def _planner_payload(action, **kwargs):
    if action == 'rag':
        return json.dumps({
            "action": "tool", "tool_name": "rag_answer_tool",
            "arguments": {"question": kwargs["question"]},
            "reason_code": "need_knowledge",
        }, ensure_ascii=False)
    if action == 'balance':
        return json.dumps({
            "action": "tool", "tool_name": "leave_balance_tool",
            "arguments": {},
            "reason_code": "need_balance",
        }, ensure_ascii=False)
    if action == 'proposal':
        return json.dumps({
            "action": "tool", "tool_name": "leave_proposal_tool",
            "arguments": {},
            "reason_code": "need_proposal",
        }, ensure_ascii=False)
    if action == 'finish':
        return json.dumps({
            "action": "finish", "reason_code": "task_complete",
            "answer": kwargs["answer"],
        }, ensure_ascii=False)
    raise ValueError(action)


def _proposal_payload(start, end, reason='私事'):
    proposal = AnnualLeaveActionProposal(
        action_type='ANNUAL_LEAVE_REQUEST',
        start_date=date.fromisoformat(start),
        end_date=date.fromisoformat(end),
        reason=reason,
        half_day='NONE',
    )
    return json.dumps({
        'kind': 'proposal',
        'action_proposal': proposal.model_dump(mode='json'),
        'missing_fields': [],
        'message': '已生成年假申请草稿，请确认后提交。',
    }, ensure_ascii=False)


def _clarification_payload(missing_fields, question='请补充以下信息。'):
    return json.dumps({
        'kind': 'clarification',
        'action_proposal': None,
        'missing_fields': missing_fields,
        'message': question,
    }, ensure_ascii=False)


class TestAgentLoopProposalPath:
    """Agent Loop：Planner 自决 RAG → balance → leave_proposal_tool → finish。"""

    def test_rag_then_balance_then_proposal_success(self):
        question = (
            '先查连续休5天的公司规定，再看看剩多少年假，'
            '够的话帮我准备2026-09-01到2026-09-05的年假申请，原因为私事'
        )
        planner_responses = [
            _planner_payload('rag', question='连续休5天的公司规定'),
            _planner_payload('balance'),
            _planner_payload('proposal'),
            _planner_payload('finish', answer='已生成年假申请草稿，请确认后提交。'),
        ]
        rag_payload = json.dumps(
            {"answer": "规定可连续休5天", "success": True, "sources": []},
            ensure_ascii=False,
        )
        balance_payload = json.dumps(
            {"success": True, "data": {"remaining_days": 10}},
            ensure_ascii=False,
        )
        rag_mock = Mock()
        rag_mock.invoke.return_value = rag_payload
        balance_mock = Mock()
        balance_mock.invoke.return_value = balance_payload
        proposal_mock = Mock()
        proposal_mock.invoke.return_value = _proposal_payload('2026-09-01', '2026-09-05')

        with patch('app.agents.planner_node.call_llm', side_effect=planner_responses), \
             patch('app.agents.planner_node.JAVA_BASE_URL', 'http://java.test'), \
             patch('app.agents.planner_node.JAVA_INTERNAL_TOKEN', 'internal-secret'), \
             patch('app.agents.tool_executor_node.rag_answer_tool', rag_mock), \
             patch('app.agents.tool_executor_node.leave_balance_tool', balance_mock), \
             patch('app.agents.tool_executor_node.leave_proposal_tool', proposal_mock):
            result = run_langgraph_agent(
                question,
                allow_business_actions=True,
                business_date=BUSINESS_DATE,
                use_planner=True,
                employee_id='E1001',
            )

        assert rag_mock.invoke.call_count == 1
        assert balance_mock.invoke.call_count == 1
        assert proposal_mock.invoke.call_count == 1
        assert result['stop_reason'] == 'task_complete'
        # Executor 把 action_proposal 写回 State
        assert result['action_proposal']['action_type'] == 'ANNUAL_LEAVE_REQUEST'
        # Executor 解析 Tool observation 时,把 ISO date 还原为 Python date 对象
        # (对下游 strict Pydantic schema 友好)。本测试断言不依赖序列化方向。
        assert result['action_proposal']['start_date'] == date(2026, 9, 1)
        assert result['action_proposal']['end_date'] == date(2026, 9, 5)
        assert result['missing_fields'] == []
        # 不允许把 actionId / nonce / employeeId 等敏感字段泄漏
        serialized = str(result['action_proposal'])
        for forbidden in ('actionId', 'nonce', 'employeeId'):
            assert forbidden not in serialized

    def test_missing_reason_returns_clarification(self):
        """缺 reason：leave_proposal_tool 返回 clarification，Planner 转 finish。"""
        question = (
            '先查连续休5天的公司规定，再看看剩多少年假，'
            '够的话帮我准备2026-09-01到2026-09-05的年假申请'
        )
        planner_responses = [
            _planner_payload('rag', question='连续休5天的公司规定'),
            _planner_payload('balance'),
            _planner_payload('proposal'),
            _planner_payload('finish', answer='请补充申请原因后再提交。'),
        ]
        rag_payload = json.dumps(
            {"answer": "规定可连续休5天", "success": True, "sources": []},
            ensure_ascii=False,
        )
        balance_payload = json.dumps(
            {"success": True, "data": {"remaining_days": 10}},
            ensure_ascii=False,
        )
        rag_mock = Mock()
        rag_mock.invoke.return_value = rag_payload
        balance_mock = Mock()
        balance_mock.invoke.return_value = balance_payload
        proposal_mock = Mock()
        proposal_mock.invoke.return_value = _clarification_payload(['reason'])

        with patch('app.agents.planner_node.call_llm', side_effect=planner_responses), \
             patch('app.agents.planner_node.JAVA_BASE_URL', 'http://java.test'), \
             patch('app.agents.planner_node.JAVA_INTERNAL_TOKEN', 'internal-secret'), \
             patch('app.agents.tool_executor_node.rag_answer_tool', rag_mock), \
             patch('app.agents.tool_executor_node.leave_balance_tool', balance_mock), \
             patch('app.agents.tool_executor_node.leave_proposal_tool', proposal_mock):
            result = run_langgraph_agent(
                question,
                allow_business_actions=True,
                business_date=BUSINESS_DATE,
                use_planner=True,
                employee_id='E1001',
            )

        assert result['stop_reason'] == 'task_complete'
        assert result['action_proposal'] is None
        assert result['missing_fields'] == ['reason']

    def test_balance_insufficient_finish_without_proposal(self):
        """余额不足：Planner 应 finish 告知用户，不调用 leave_proposal_tool。"""
        question = (
            '先查连续休5天的公司规定，再看看剩多少年假，'
            '够的话帮我准备2026-09-01到2026-09-05的年假申请'
        )
        planner_responses = [
            _planner_payload('rag', question='连续休5天的公司规定'),
            _planner_payload('balance'),
            _planner_payload('finish', answer='当前剩余年假不足，无法提交申请。'),
        ]
        rag_payload = json.dumps(
            {"answer": "规定可连续休5天", "success": True, "sources": []},
            ensure_ascii=False,
        )
        balance_payload = json.dumps(
            {"success": True, "data": {"remaining_days": 0}},
            ensure_ascii=False,
        )
        rag_mock = Mock()
        rag_mock.invoke.return_value = rag_payload
        balance_mock = Mock()
        balance_mock.invoke.return_value = balance_payload
        proposal_mock = Mock()

        with patch('app.agents.planner_node.call_llm', side_effect=planner_responses), \
             patch('app.agents.planner_node.JAVA_BASE_URL', 'http://java.test'), \
             patch('app.agents.planner_node.JAVA_INTERNAL_TOKEN', 'internal-secret'), \
             patch('app.agents.tool_executor_node.rag_answer_tool', rag_mock), \
             patch('app.agents.tool_executor_node.leave_balance_tool', balance_mock), \
             patch('app.agents.tool_executor_node.leave_proposal_tool', proposal_mock):
            result = run_langgraph_agent(
                question,
                allow_business_actions=True,
                business_date=BUSINESS_DATE,
                use_planner=True,
                employee_id='E1001',
            )

        proposal_mock.invoke.assert_not_called()  # 未调用 leave_proposal_tool
        assert '不足' in result['answer']
        assert result['action_proposal'] is None

    def test_direct_apply_goes_through_proposal_tool(self):
        """简单直接申请：Planner 一次决策 leave_proposal_tool → finish。"""
        question = '申请2026-09-01一天年假，原因为私事'
        planner_responses = [
            _planner_payload('proposal'),
            _planner_payload('finish', answer='已生成年假申请草稿，请确认后提交。'),
        ]
        proposal_mock = Mock()
        proposal_mock.invoke.return_value = _proposal_payload('2026-09-01', '2026-09-01')

        with patch('app.agents.planner_node.call_llm', side_effect=planner_responses), \
             patch('app.agents.tool_executor_node.leave_proposal_tool', proposal_mock):
            result = run_langgraph_agent(
                question,
                allow_business_actions=True,
                business_date=BUSINESS_DATE,
                use_planner=True,
                employee_id='E1001',
            )

        proposal_mock.invoke.assert_called_once()
        assert result['stop_reason'] == 'task_complete'
        assert result['action_proposal']['start_date'] == date(2026, 9, 1)


class TestExecutorPermissionBoundary:
    """Executor 独立再校验：无权限 / 无业务日期时 proposal tool 被拦截。"""

    def _base_state(self, **changes):
        state = {
            'question': '申请2026-09-01一天年假，原因为私事',
            'safe': True, 'route': '', 'answer': '',
            'tool_result': {}, 'sources': [], 'reason': '', 'category': '',
            'allow_eval': False,
            'allow_business_actions': True,
            'business_date': BUSINESS_DATE,
            'trace_id': 'trace-executor',
            'employee_id': 'E1001',
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
            'stop_reason': '',
        }
        state.update(changes)
        return state

    def test_executor_blocks_proposal_without_business_permission(self):
        result = tool_executor_node(self._base_state(allow_business_actions=False))
        assert result['stop_reason'] == 'not_allowed'
        assert result['tool_call_count'] == 0  # 未真正发起执行
        assert result['tool_history'][-1]['status'] == 'blocked'

    def test_executor_blocks_proposal_without_business_date(self):
        result = tool_executor_node(self._base_state(business_date=None))
        assert result['stop_reason'] == 'not_allowed'
        assert result['tool_call_count'] == 0

    def test_executor_rejects_leaked_business_fields(self):
        """模型在 arguments 中夹带业务字段 → 拒绝执行，不进入受控链路。"""
        state = self._base_state()
        state['planner_decision'] = {
            'action': 'tool',
            'tool_name': 'leave_proposal_tool',
            'arguments': {'start_date': '2026-09-01'},
            'answer': None,
            'reason_code': 'need_proposal',
        }
        result = tool_executor_node(state)
        # schema 校验先拦截：arguments 非空
        assert result['stop_reason'] == 'invalid_decision'
        assert result['tool_call_count'] == 0

    def test_executor_injects_system_fields_to_proposal_tool(self):
        """Executor 注入 question / business_date / trace_id，模型不可见。"""
        fake_tool = Mock()
        fake_tool.invoke.return_value = _proposal_payload('2026-09-01', '2026-09-01')
        with patch('app.agents.tool_executor_node.leave_proposal_tool', fake_tool):
            result = tool_executor_node(self._base_state())

        assert fake_tool.invoke.call_count == 1
        args_dict = fake_tool.invoke.call_args[0][0]  # LangChain tool.invoke 传 dict
        assert args_dict['question'] == '申请2026-09-01一天年假，原因为私事'
        assert args_dict['business_date'] == BUSINESS_DATE.isoformat()
        assert args_dict['trace_id'] == 'trace-executor'
        assert result['action_proposal']['start_date'] == date(2026, 9, 1)
        assert result['missing_fields'] == []
        assert result['stop_reason'] == 'tool_executed'


class TestActionProposalFinalization:
    """最终响应前的确定性 postcondition：只有 task_complete 且最后一次成功
    Tool 是 leave_proposal_tool 才保留 action_proposal / missing_fields。"""

    def _run(self, question, planner_responses, proposal_payload,
             rag_payload=None):
        proposal_mock = Mock()
        proposal_mock.invoke.return_value = proposal_payload
        patches = [
            patch('app.agents.planner_node.call_llm', side_effect=planner_responses),
            patch('app.agents.tool_executor_node.leave_proposal_tool', proposal_mock),
        ]
        if rag_payload is not None:
            rag_mock = Mock()
            rag_mock.invoke.return_value = rag_payload
            patches.append(patch('app.agents.tool_executor_node.rag_answer_tool', rag_mock))
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            return run_langgraph_agent(
                question,
                allow_business_actions=True,
                business_date=BUSINESS_DATE,
                use_planner=True,
                employee_id='E1001',
            )

    def test_proposal_then_finish_keeps_action_proposal(self):
        """proposal → finish：保留 action_proposal 与 missing_fields。"""
        result = self._run(
            '申请2026-09-01一天年假，原因为私事',
            [
                _planner_payload('proposal'),
                _planner_payload('finish', answer='已生成年假申请草稿，请确认后提交。'),
            ],
            _proposal_payload('2026-09-01', '2026-09-01'),
        )
        assert result['stop_reason'] == 'task_complete'
        assert result['action_proposal']['start_date'] == date(2026, 9, 1)
        assert result['missing_fields'] == []

    def test_clarification_then_finish_keeps_missing_fields(self):
        """clarification → finish：保留 missing_fields（action_proposal 保持 None）。"""
        result = self._run(
            '申请2026-09-01到2026-09-05年假',
            [
                _planner_payload('proposal'),
                _planner_payload('finish', answer='请补充申请原因。'),
            ],
            _clarification_payload(['reason']),
        )
        assert result['stop_reason'] == 'task_complete'
        assert result['action_proposal'] is None
        assert result['missing_fields'] == ['reason']

    def test_proposal_then_refuse_clears_action_proposal(self):
        """proposal → refuse：清空 action_proposal 与 missing_fields。"""
        result = self._run(
            '申请2026-09-01一天年假，原因为私事',
            [
                _planner_payload('proposal'),
                json.dumps({
                    'action': 'refuse', 'answer': '当前不可申请。',
                    'reason_code': 'cannot_complete',
                }, ensure_ascii=False),
            ],
            _proposal_payload('2026-09-01', '2026-09-01'),
        )
        assert result['stop_reason'] == 'refused'
        assert result['action_proposal'] is None
        assert result['missing_fields'] == []

    def test_proposal_then_other_tool_then_finish_clears(self):
        """proposal → 其他 Tool → finish：最后一次成功 Tool 非 proposal → 清空。"""
        rag_payload = json.dumps(
            {"answer": "年假制度：入职满1年5天。", "success": True, "sources": []},
            ensure_ascii=False,
        )
        result = self._run(
            '帮我准备年假申请，另外查下年假制度',
            [
                _planner_payload('proposal'),
                _planner_payload('rag', question='年假制度'),
                _planner_payload('finish', answer='已完成。'),
            ],
            _proposal_payload('2026-09-01', '2026-09-01'),
            rag_payload=rag_payload,
        )
        assert result['stop_reason'] == 'task_complete'
        assert result['action_proposal'] is None  # 最后成功的是 rag，不是 proposal
        assert result['missing_fields'] == []

    def test_proposal_then_invalid_decision_clears(self):
        """proposal → invalid_decision：终止路径清空 action_proposal。"""
        result = self._run(
            '申请2026-09-01一天年假，原因为私事',
            [
                _planner_payload('proposal'),
                'not a json',
            ],
            _proposal_payload('2026-09-01', '2026-09-01'),
        )
        assert result['stop_reason'] == 'invalid_decision'
        assert result['action_proposal'] is None
        assert result['missing_fields'] == []


class TestBackwardCompatDeterministicGraph:
    """use_planner=False 时：保持旧确定性 Graph（Router → action_node）不变。"""

    def test_deterministic_path_uses_router_not_planner(self):
        rag_payload = json.dumps(
            {"answer": "规定", "success": True, "sources": []},
            ensure_ascii=False,
        )
        rag_mock = Mock()
        rag_mock.invoke.return_value = rag_payload

        def _fake_planner(_state):
            raise AssertionError('planner must not run in deterministic path')

        with patch('app.agents.langgraph_agent.rewrite_query', return_value={
            "rewritten_query": "年假政策",
            "rewrite_applied": False,
            "rewrite_reason": "",
        }), patch('app.agents.langgraph_agent.rag_answer_tool', rag_mock), \
             patch('app.agents.langgraph_agent.planner_node', side_effect=_fake_planner):
            result = run_langgraph_agent(
                '公司的年假政策是什么',
                allow_business_actions=True,
                business_date=BUSINESS_DATE,
                use_planner=False,  # 旧路径
            )
        assert result['route'] == 'rag'
        assert '规定' in result['answer']

    def test_deterministic_action_path_short_circuits_to_action_node(self):
        """use_planner=False 时，Router 直接把 action intent 路由到 action_node。"""
        proposal = ProposalPlanningResult(proposal=AnnualLeaveActionProposal(
            action_type="ANNUAL_LEAVE_REQUEST",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 1),
            reason="私事",
            half_day="NONE",
        ))

        def _fake_planner(_state):
            raise AssertionError('planner must not run in deterministic path')

        with patch('app.agents.langgraph_agent.plan_annual_leave_action',
                   return_value=proposal) as planner_service, \
             patch('app.agents.langgraph_agent.planner_node', side_effect=_fake_planner):
            result = run_langgraph_agent(
                '申请2026-09-01一天年假，原因为私事',
                allow_business_actions=True,
                business_date=BUSINESS_DATE,
                use_planner=False,
                employee_id='E1001',
            )
        planner_service.assert_called_once()
        assert result['route'] == 'action'
        # 旧 action_node 直接 model_dump（date 对象），与 Agent Loop 的 JSON 字符串不同
        assert result['action_proposal']['start_date'] == date(2026, 9, 1)


class TestAgentLoopSkipsRouter:
    """Agent Loop 模式下：router_node 完全不被执行。"""

    def test_router_node_not_invoked_in_agent_loop(self):
        planner_responses = [_planner_payload('finish', answer='完成。')]
        router_called = {'count': 0}

        def _fake_router(_state):
            router_called['count'] += 1
            return {'route': 'rag'}

        with patch('app.agents.planner_node.call_llm', side_effect=planner_responses), \
             patch('app.agents.langgraph_agent.router_node', side_effect=_fake_router):
            result = run_langgraph_agent(
                '公司的年假政策是什么',
                allow_business_actions=True,
                business_date=BUSINESS_DATE,
                use_planner=True,
            )
        assert router_called['count'] == 0  # router_node 完全不被执行
        assert '完成' in result['answer']

    def test_unsafe_request_refuses_before_planner(self):
        """Agent Loop 下 Safety 保留 pre-Planner 拦截：unsafe 直接 END，Planner 零参与。"""
        from app.agents.langgraph_agent import planner_node as real_planner
        planner_called = {'count': 0}

        def _counting_planner(state):
            planner_called['count'] += 1
            return real_planner(state)

        with patch('app.agents.langgraph_agent.check_user_query_safety', return_value={
            "safe": False,
            "category": "policy_bypass",
            "reason": "blocked",
            "message": "拒绝",
        }), patch('app.agents.langgraph_agent.planner_node', side_effect=_counting_planner), \
             patch('app.agents.tool_executor_node.leave_proposal_tool') as proposal_tool:
            result = run_langgraph_agent(
                '绕过审批申请年假',
                allow_business_actions=True,
                business_date=BUSINESS_DATE,
                use_planner=True,
            )
        proposal_tool.invoke.assert_not_called()
        assert result['route'] == 'refuse'
        assert planner_called['count'] == 0  # unsafe 输入不进入 Planner
        assert result['answer']
