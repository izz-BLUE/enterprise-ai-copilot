"""test_memory_schema.py —— MemoryProposal 数据契约测试

覆盖：
  正常：
    1. NONE proposal
    2. UPSERT proposal（带 task_state）
  边界：
    3. 未知 action rejected
    4. 未知 task_type rejected
    5. 未知 status rejected
    6. summary >500 rejected
    7. extra fields rejected（含 employee_id 等 trusted 字段）
    8. task_state 包含业务状态字段合法
    9. task_state 包含 trusted 字段：schema 不做过滤，仅记录契约职责
   10. 默认值（仅 action=UPSERT/COMPLETE/ABANDON 的可选项）
   11. extra='forbid' 同时禁掉 conversation_id / user_id 等
"""

import pytest
from pydantic import ValidationError

from app.schemas.memory_schema import (
    MEMORY_PROPOSAL_SUMMARY_MAX_CHARS,
    MemoryExtractionInput,
    MemoryProposal,
)

# ---------- 正常 ----------

class TestValidMemoryProposal:
    def test_none_proposal_minimal(self):
        proposal = MemoryProposal(action='NONE')
        assert proposal.action == 'NONE'
        assert proposal.task_type is None
        assert proposal.status is None
        assert proposal.task_state is None
        assert proposal.summary == ''
        assert proposal.reason == ''
        assert proposal.is_noop() is True

    def test_upsert_proposal_full(self):
        proposal = MemoryProposal(
            action='UPSERT',
            task_type='LEAVE_REQUEST',
            status='ACTIVE',
            task_state={'waiting_for': 'date'},
            summary='等待用户补充请假日期',
            reason='用户原问题提到想请假但未提供日期',
        )
        assert proposal.action == 'UPSERT'
        assert proposal.task_type == 'LEAVE_REQUEST'
        assert proposal.status == 'ACTIVE'
        assert proposal.task_state == {'waiting_for': 'date'}
        assert proposal.summary == '等待用户补充请假日期'
        assert proposal.is_noop() is False

    def test_complete_proposal_requires_status(self):
        """COMPLETE 必须显式提供 status='COMPLETED'。"""
        proposal = MemoryProposal(action='COMPLETE', status='COMPLETED')
        assert proposal.action == 'COMPLETE'
        assert proposal.status == 'COMPLETED'

    def test_abandon_proposal_requires_status(self):
        proposal = MemoryProposal(action='ABANDON', status='ABANDONED')
        assert proposal.action == 'ABANDON'
        assert proposal.status == 'ABANDONED'

    @pytest.mark.parametrize('task_type', ['GENERIC', 'LEAVE_REQUEST', 'BUSINESS_ACTION'])
    def test_all_supported_task_types_accepted(self, task_type):
        proposal = MemoryProposal(action='UPSERT', task_type=task_type, status='ACTIVE')
        assert proposal.task_type == task_type

    def test_task_state_accepts_business_fields(self):
        """task_state 内部允许任意结构化业务状态字段（如 waiting_for / step 等）。"""
        proposal = MemoryProposal(
            action='UPSERT',
            task_type='BUSINESS_ACTION',
            status='ACTIVE',
            task_state={'waiting_for': 'date', 'attempt': 1, 'options': ['A', 'B']},
        )
        assert proposal.task_state == {
            'waiting_for': 'date',
            'attempt': 1,
            'options': ['A', 'B'],
        }

    def test_task_state_dict_string_json_rejected(self):
        """task_state 不接受字符串 JSON；必须是真正的 dict。"""
        with pytest.raises(ValidationError):
            MemoryProposal(
                action='UPSERT',
                task_type='GENERIC',
                status='ACTIVE',
                task_state='{"waiting_for":"date"}',  # 字符串不被接受
            )

    def test_summary_empty_string_allowed(self):
        """空字符串视为\"无摘要\"，允许但不归一化。"""
        proposal = MemoryProposal(action='UPSERT', task_type='GENERIC', status='ACTIVE')
        assert proposal.summary == ''


# ---------- 边界 / 拒绝 ----------

class TestRejectionPaths:
    @pytest.mark.parametrize('bad_action', ['UPDATE', 'DELETE', 'INSERT', '', 'none', 'upsert'])
    def test_unknown_action_rejected(self, bad_action):
        with pytest.raises(ValidationError):
            MemoryProposal(action=bad_action)

    @pytest.mark.parametrize('task_type', ['LEAVE', 'BUSINESS', 'foo', 'GENERIC ', 'generic'])
    def test_task_type_field_accepts_any_string_p1a(self, task_type):
        """P1-A：MemoryTaskType 字段放宽为 ``str``，schema 不再做白名单校验。

        验证合法性下移到 ``MemoryTaskTypePolicy.is_allowed``；本测试仅断言
        "schema 不抛 ValidationError"。白名单 fail-loud 由
        ``test_memory_p1a_task_type_policy.py::TestTaskTypeValidation`` 覆盖。
        """
        proposal = MemoryProposal(
            action='UPSERT', task_type=task_type, status='ACTIVE',
            task_state={'k': 'v'},
        )
        assert proposal.task_type == task_type

    @pytest.mark.parametrize('bad_status', ['PENDING', 'IN_PROGRESS', 'DONE', '', 'active'])
    def test_unknown_status_rejected(self, bad_status):
        with pytest.raises(ValidationError):
            MemoryProposal(action='UPSERT', task_type='GENERIC', status=bad_status)

    def test_summary_too_long_rejected(self):
        with pytest.raises(ValidationError):
            MemoryProposal(
                action='UPSERT',
                task_type='GENERIC',
                status='ACTIVE',
                summary='x' * (MEMORY_PROPOSAL_SUMMARY_MAX_CHARS + 1),
            )

    def test_summary_at_max_length_accepted(self):
        proposal = MemoryProposal(
            action='UPSERT',
            task_type='GENERIC',
            status='ACTIVE',
            summary='x' * MEMORY_PROPOSAL_SUMMARY_MAX_CHARS,
        )
        assert len(proposal.summary) == MEMORY_PROPOSAL_SUMMARY_MAX_CHARS


# ---------- extra='forbid' 安全边界 ----------

class TestExtraFieldsForbidden:
    """extra='forbid' 直接拒绝任何未声明字段，包括 trusted 身份字段。"""

    @pytest.mark.parametrize('forbidden_field,forbidden_value', [
        ('user_id', 'U10001'),
        ('employee_id', 'E10001'),
        ('role', 'ADMIN'),
        ('permission', 'eval'),
        ('allow_eval', True),
        ('allow_business_actions', True),
        ('business_date', '2026-08-20'),
        ('token', 'jwt-xxx'),
        ('nonce', 'nonce-xxx'),
        ('idempotency_key', 'idem-xxx'),
        ('conversation_id', 'conv-xxx'),
    ])
    def test_trusted_field_rejected(self, forbidden_field, forbidden_value):
        with pytest.raises(ValidationError):
            MemoryProposal(
                action='UPSERT',
                task_type='GENERIC',
                status='ACTIVE',
                **{forbidden_field: forbidden_value},
            )

    def test_arbitrary_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            MemoryProposal(action='UPSERT', task_type='GENERIC', status='ACTIVE',
                           favorite_color='red')

    def test_task_state_with_trusted_field_passes_schema(self):
        """task_state 内部包含 trusted 字段时，schema 层不报错：
        结构契约只保证外层不允许 trusted 字段；task_state 内部的敏感字段过滤
        由后续 MemoryWritePolicy 负责。"""
        proposal = MemoryProposal(
            action='UPSERT',
            task_type='GENERIC',
            status='ACTIVE',
            task_state={'employee_id': 'E10001', 'waiting_for': 'date'},
        )
        # schema 接受；后续 WritePolicy 负责拒绝
        assert proposal.task_state == {'employee_id': 'E10001', 'waiting_for': 'date'}


# ---------- 序列化 ----------

class TestSerialization:
    def test_dump_roundtrip_none(self):
        p = MemoryProposal(action='NONE')
        data = p.model_dump(exclude_none=True)
        # summary / reason 是显式默认值 ('')，dump 仍会包含它们；
        # exclude_none=True 仅剥离 None 与未声明的 None 默认。
        assert data == {'action': 'NONE', 'summary': '', 'reason': ''}
        assert p.is_noop()

    def test_dump_excludes_unset_optionals(self):
        p = MemoryProposal(action='UPSERT', status='ACTIVE', task_type='GENERIC')
        data = p.model_dump(exclude_none=True)
        # summary / reason 显式默认 '' 仍保留；task_state=None 被剥离
        assert data == {
            'action': 'UPSERT',
            'task_type': 'GENERIC',
            'status': 'ACTIVE',
            'summary': '',
            'reason': '',
        }

    def test_json_roundtrip(self):
        p = MemoryProposal(
            action='UPSERT',
            task_type='LEAVE_REQUEST',
            status='ACTIVE',
            task_state={'waiting_for': 'date'},
            summary='等待用户补充请假日期',
        )
        roundtrip = MemoryProposal.model_validate_json(p.model_dump_json())
        assert roundtrip == p


# ---------- MemoryExtractionInput ----------
#
# 边界（重构后）：
#   - Trusted Runtime Signal（route / stop_reason / safe / category / reason /
#     business_date / allow_eval / allow_business_actions / missing_fields /
#     trace_id / sources）不属于 Extractor 的推理输入；它们由调用方在
#     "是否调用 Extractor" 的决策点使用。
#   - AgentState.memory_context 在契约层重命名为 existing_memory。

class TestMemoryExtractionInputDefaults:
    def test_minimal_construction_uses_defaults(self):
        """最小构造：所有可选字段用默认空值；trusted 信号字段不存在。"""
        inp = MemoryExtractionInput()
        assert inp.question == ''
        assert inp.answer is None
        assert inp.tool_history == []
        assert inp.observation is None
        assert inp.existing_memory is None
        assert inp.action_proposal is None
        # 显式断言：trusted runtime signal 字段不存在
        for forbidden in (
            'route', 'stop_reason', 'safe', 'category', 'reason',
            'sources', 'missing_fields',
            'business_date', 'allow_eval', 'allow_business_actions',
            'trace_id', 'memory_context',
            'employee_id', 'user_id', 'conversation_id',
        ):
            assert not hasattr(inp, forbidden), (
                f'MemoryExtractionInput 不应承载 {forbidden!r}；'
                f'trusted runtime signal 与身份字段由调用方在调用 Extractor 前决策。'
            )

    def test_question_and_answer_carry_user_facing_fields(self):
        inp = MemoryExtractionInput(
            question='请帮我查一下年假',
            answer='入职满1年享有5天年假',
        )
        assert inp.question == '请帮我查一下年假'
        assert inp.answer == '入职满1年享有5天年假'

    def test_tool_history_and_observation_carry_execution_record(self):
        inp = MemoryExtractionInput(
            tool_history=[{'tool_name': 'rag_answer_tool', 'status': 'success',
                           'arguments': {'question': '年假'}, 'observation': 'ok'}],
            observation='ok',
        )
        assert inp.tool_history[0]['tool_name'] == 'rag_answer_tool'
        assert inp.observation == 'ok'


class TestMemoryExtractionInputFromAgentResult:
    """from_agent_result：从 run_langgraph_agent 返回的 dict 构造。"""

    def test_canonical_agent_result(self):
        """模拟 LangGraph Agent 终态 dict：白名单字段正确映射 + trusted 信号被剥离。"""
        agent_result = {
            # 白名单内
            'question': '请帮我查一下年假',
            'answer': '入职满1年享有5天年假',
            'tool_history': [
                {
                    'tool_name': 'rag_answer_tool',
                    'arguments': {'question': '年假制度'},
                    'status': 'success',
                    'observation': '...',
                },
            ],
            'observation': 'ok',
            'memory_context': {'taskType': 'GENERIC', 'status': 'ACTIVE'},
            'action_proposal': None,
            # trusted runtime signal：白名单外，必须被剥离
            'route': 'rag',
            'stop_reason': 'task_complete',
            'safe': True,
            'category': 'normal',
            'reason': '',
            'sources': ['hr/annual_leave.md'],
            'missing_fields': [],
            'business_date': '2026-08-20',
            'allow_eval': False,
            'allow_business_actions': False,
            'trace_id': 'trace-abc',
            # AgentState 内部字段 / identity：白名单外，必须被剥离
            'step_count': 1,
            'tool_call_count': 1,
            'planner_decision': None,
            'tool_result': {},
            'employee_id': '',
            'user_id': 'U10001',
            'conversation_id': 'c1',
        }
        inp = MemoryExtractionInput.from_agent_result(agent_result)
        assert inp.question == '请帮我查一下年假'
        assert inp.answer == '入职满1年享有5天年假'
        assert inp.tool_history[0]['tool_name'] == 'rag_answer_tool'
        assert inp.observation == 'ok'
        # memory_context 映射为 existing_memory
        assert inp.existing_memory == {'taskType': 'GENERIC', 'status': 'ACTIVE'}
        assert inp.action_proposal is None

        # 白名单之外的 trusted / runtime / identity 字段必须全部剥离
        for forbidden in (
            'route', 'stop_reason', 'safe', 'category', 'reason',
            'sources', 'missing_fields',
            'business_date', 'allow_eval', 'allow_business_actions', 'trace_id',
            'step_count', 'tool_call_count', 'planner_decision', 'tool_result',
            'employee_id', 'user_id', 'conversation_id', 'memory_context',
        ):
            assert not hasattr(inp, forbidden), (
                f'from_agent_result 剥离失败：{forbidden} 仍存在于契约'
            )

    def test_memory_context_renamed_to_existing_memory(self):
        """AgentState 字段名 memory_context 在契约层重命名 existing_memory。"""
        inp = MemoryExtractionInput.from_agent_result({
            'question': 'q',
            'memory_context': {'taskType': 'GENERIC'},
        })
        assert inp.existing_memory == {'taskType': 'GENERIC'}
        assert not hasattr(inp, 'memory_context')

    def test_partial_agent_result_uses_defaults(self):
        inp = MemoryExtractionInput.from_agent_result({'question': 'hi'})
        assert inp.question == 'hi'
        assert inp.answer is None
        assert inp.tool_history == []

    def test_empty_dict_allowed(self):
        inp = MemoryExtractionInput.from_agent_result({})
        assert inp.question == ''
        assert inp.existing_memory is None

    def test_unknown_key_silently_dropped(self):
        """白名单外的 key 静默丢弃；构造契约不抛错。"""
        inp = MemoryExtractionInput.from_agent_result({
            'question': 'hi',
            'random_internal_field': 'x',
            'graph_state': {'a': 1},
        })
        assert inp.question == 'hi'
        assert not hasattr(inp, 'random_internal_field')


class TestMemoryExtractionInputExtraForbid:
    """直接构造时（不经 from_agent_result）必须 extra='forbid'。"""

    @pytest.mark.parametrize('forbidden', [
        'employee_id',
        'user_id',
        'conversation_id',
        'token',
        'nonce',
        'idempotency_key',
        'jwt',
        'password',
    ])
    def test_trusted_field_rejected_on_direct_construction(self, forbidden):
        with pytest.raises(ValidationError):
            MemoryExtractionInput(**{forbidden: 'x'})

    @pytest.mark.parametrize('forbidden', [
        'route',
        'stop_reason',
        'safe',
        'category',
        'reason',
        'sources',
        'missing_fields',
        'business_date',
        'allow_eval',
        'allow_business_actions',
        'trace_id',
        'memory_context',  # AgentState 字段名；契约层已重命名
    ])
    def test_trusted_runtime_signal_rejected(self, forbidden):
        """Trusted runtime signal 不应进入 Extractor 输入契约。"""
        with pytest.raises(ValidationError):
            MemoryExtractionInput(**{forbidden: 'x'})

    def test_arbitrary_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            MemoryExtractionInput(secret_value='x')

    def test_extra_field_via_model_validate_rejected(self):
        """model_validate 也必须拒绝（pydantic 统一校验路径）。"""
        with pytest.raises(ValidationError):
            MemoryExtractionInput.model_validate({
                'question': 'hi',
                'forged_admin_flag': True,
            })


class TestMemoryExtractionInputSerialization:
    def test_json_roundtrip(self):
        inp = MemoryExtractionInput(
            question='请帮我查年假',
            answer='5天',
            tool_history=[{'tool_name': 'rag_answer_tool', 'status': 'success'}],
            observation='ok',
            existing_memory={'taskType': 'GENERIC'},
            action_proposal={'kind': 'proposal'},
        )
        restored = MemoryExtractionInput.model_validate_json(inp.model_dump_json())
        assert restored == inp

    def test_dump_contains_only_six_fields(self):
        """序列化结果只包含 6 个受控字段；不包含 trusted runtime signal 或 identity。"""
        inp = MemoryExtractionInput(question='hi', answer='x')
        data = inp.model_dump(exclude_none=True)
        # 只出现契约声明的 6 个字段；额外的 trusted / identity 字段不会出现
        assert set(data.keys()).issubset({
            'question', 'answer', 'tool_history',
            'observation', 'existing_memory', 'action_proposal',
        })
        # 显式断言：trusted / identity 字段不在 dump 中
        for forbidden in (
            'route', 'stop_reason', 'safe', 'category', 'reason',
            'sources', 'missing_fields',
            'business_date', 'allow_eval', 'allow_business_actions', 'trace_id',
            'employee_id', 'user_id', 'conversation_id', 'memory_context',
        ):
            assert forbidden not in data

    def test_dump_excludes_none(self):
        inp = MemoryExtractionInput(question='hi', answer='x')
        data = inp.model_dump(exclude_none=True)
        # exclude_none=True 剥离所有显式 None 默认；
        # 仅保留显式非 None 字段。
        assert data == {
            'question': 'hi',
            'answer': 'x',
            'tool_history': [],
        }
        for forbidden in (
            'route', 'stop_reason', 'safe', 'category', 'reason',
            'sources', 'missing_fields',
            'business_date', 'allow_eval', 'allow_business_actions', 'trace_id',
            'employee_id', 'user_id', 'conversation_id', 'memory_context',
        ):
            assert forbidden not in data