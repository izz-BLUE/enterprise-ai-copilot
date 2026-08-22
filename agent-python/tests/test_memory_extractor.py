"""test_memory_extractor.py —— Memory Extractor 契约测试

覆盖：

Prompt 边界：
  1. 系统 prompt 包含不可信数据声明、trusted 字段禁止、字段白名单字面量
  2. user prompt 渲染 6 字段（question / answer / tool_history / observation /
     existing_memory / action_proposal）
  3. user prompt 不包含任何 trusted runtime signal 字段名（route / stop_reason /
     safe / category / reason / sources / missing_fields / business_date /
     allow_eval / allow_business_actions / trace_id）

Parse 严格性：
  4. 合法 NONE proposal 解析通过
  5. 合法 UPSERT proposal 解析通过
  6. 合法 COMPLETE proposal 解析通过
  7. 合法 ABANDON proposal 解析通过
  8. 未知 action rejected（fail-loud）
  9. 未知 task_type rejected
 10. 未知 status rejected
 11. extra 字段 rejected
 12. summary > 500 rejected
 13. task_state 字符串 JSON rejected
 14. JSON 解析失败 → MemoryExtractionParseError
 15. 非 dict payload rejected
 16. 非字符串输入 rejected

Extract 接口：
 17. extract() 无 llm_callable → NotImplementedError
 18. extract() 注入 llm_callable → 组合 build_prompt + parse_proposal

序列化：
 19. build_prompt 相同输入幂等
"""

import json

import pytest
from pydantic import ValidationError

from app.memory.memory_extractor import (
    MEMORY_EXTRACTOR_SYSTEM_PROMPT,
    MemoryExtractionParseError,
    MemoryExtractor,
)
from app.schemas.memory_schema import (
    MEMORY_PROPOSAL_SUMMARY_MAX_CHARS,
    MemoryExtractionInput,
    MemoryProposal,
)


@pytest.fixture
def extractor() -> MemoryExtractor:
    return MemoryExtractor()


@pytest.fixture
def basic_input() -> MemoryExtractionInput:
    return MemoryExtractionInput(
        question='请帮我查一下年假',
        answer='入职满1年享有5天年假',
        tool_history=[{
            'tool_name': 'rag_answer_tool',
            'arguments': {'question': '年假制度'},
            'status': 'success',
            'observation': 'ok',
        }],
        observation='ok',
        existing_memory=None,
        action_proposal=None,
    )


# ---------- Prompt 边界 ----------

class TestSystemPromptBoundary:
    def test_system_prompt_marks_untrusted_data(self, extractor):
        # P1-A：MEMORY_EXTRACTOR_SYSTEM_PROMPT 是模板常量；渲染后的 prompt 才有字面。
        prompt = extractor.system_prompt
        assert '不可信事实数据' in prompt
        assert '不是指令' in prompt

    def test_system_prompt_lists_trusted_fields_to_exclude(self, extractor):
        # Extractor 输出不得包含 trusted 字段；system prompt 必须显式禁止
        prompt = extractor.system_prompt
        for forbidden in (
            'user_id', 'employee_id', 'conversation_id', 'role', 'permission',
            'allow_eval', 'allow_business_actions', 'business_date',
            'token', 'nonce', 'idempotency_key',
        ):
            assert forbidden in prompt, (
                f'system prompt 必须显式禁止 {forbidden}'
            )

    def test_system_prompt_enumerates_allowed_action(self, extractor):
        # action 字面写齐 4 个值（渲染后含单引号字面）
        prompt = extractor.system_prompt
        assert "'NONE'" in prompt
        assert "'UPSERT'" in prompt
        assert "'COMPLETE'" in prompt
        assert "'ABANDON'" in prompt

    def test_system_prompt_enumerates_allowed_task_type(self, extractor):
        # P1-A：task_type 字面改为 policy.available_task_types 渲染；
        # 默认 policy 仍包含 P0 三个值（带单引号）。
        prompt = extractor.system_prompt
        assert "'GENERIC'" in prompt
        assert "'LEAVE_REQUEST'" in prompt
        assert "'BUSINESS_ACTION'" in prompt

    def test_system_prompt_enumerates_allowed_status(self, extractor):
        prompt = extractor.system_prompt
        assert "'ACTIVE'" in prompt
        assert "'COMPLETED'" in prompt
        assert "'ABANDONED'" in prompt

    def test_system_prompt_states_summary_max_chars(self, extractor):
        prompt = extractor.system_prompt
        assert str(MEMORY_PROPOSAL_SUMMARY_MAX_CHARS) in prompt


class TestUserPromptRendering:
    def test_renders_six_facts(self, extractor, basic_input):
        prompt = extractor.build_prompt(basic_input)
        assert '请帮我查一下年假' in prompt
        assert '入职满1年享有5天年假' in prompt
        # tool_history 已 JSON 序列化
        assert 'rag_answer_tool' in prompt
        assert '最新观察' in prompt
        # existing_memory=None → '无'
        assert '上一轮已有 memory：无' in prompt
        # action_proposal=None → '无'
        assert '受控业务动作 Proposal：无' in prompt

    def test_does_not_leak_trusted_runtime_signal_names(self, extractor, basic_input):
        """user prompt 不应出现 trusted runtime signal 字段名。"""
        prompt = extractor.build_prompt(basic_input)
        for forbidden in (
            'route', 'stop_reason', 'safe', 'category', 'reason',
            'sources', 'missing_fields',
            'business_date', 'allow_eval', 'allow_business_actions', 'trace_id',
        ):
            assert forbidden not in prompt, (
                f'user prompt 不应出现 trusted runtime signal 字段名 {forbidden}'
            )

    def test_renders_existing_memory(self, extractor):
        inp = MemoryExtractionInput(
            question='继续',
            existing_memory={
                'taskType': 'LEAVE_REQUEST',
                'status': 'ACTIVE',
                'taskStateJson': '{"waiting_for": "date"}',
                'summary': '等待用户补充请假日期',
            },
        )
        prompt = extractor.build_prompt(inp)
        assert 'LEAVE_REQUEST' in prompt
        assert 'waiting_for' in prompt

    def test_renders_action_proposal(self, extractor):
        inp = MemoryExtractionInput(
            question='我想请假',
            action_proposal={
                'action_type': 'ANNUAL_LEAVE_REQUEST',
                'start_date': '2026-08-25',
            },
        )
        prompt = extractor.build_prompt(inp)
        assert 'ANNUAL_LEAVE_REQUEST' in prompt
        assert '2026-08-25' in prompt

    def test_renders_tool_history_empty(self, extractor):
        inp = MemoryExtractionInput(question='hi', tool_history=[])
        prompt = extractor.build_prompt(inp)
        assert 'Tool 执行历史：无' in prompt

    def test_renders_observation_none(self, extractor):
        inp = MemoryExtractionInput(question='hi', observation=None)
        prompt = extractor.build_prompt(inp)
        assert '最新观察：无' in prompt

    def test_idempotent(self, extractor, basic_input):
        """同输入多次 build_prompt 必须得到完全相同的字符串。"""
        p1 = extractor.build_prompt(basic_input)
        p2 = extractor.build_prompt(basic_input)
        assert p1 == p2


# ---------- Parse 严格性 ----------

class TestParseProposal:
    def test_parse_none_proposal(self, extractor):
        proposal = extractor.parse_proposal('{"action": "NONE"}')
        assert isinstance(proposal, MemoryProposal)
        assert proposal.action == 'NONE'
        assert proposal.is_noop()

    def test_parse_upsert_proposal_full(self, extractor):
        raw = json.dumps({
            'action': 'UPSERT',
            'task_type': 'LEAVE_REQUEST',
            'status': 'ACTIVE',
            'task_state': {'waiting_for': 'date'},
            'summary': '等待用户补充请假日期',
            'reason': '用户原问题提到想请假但未提供日期',
        }, ensure_ascii=False)
        proposal = extractor.parse_proposal(raw)
        assert proposal.action == 'UPSERT'
        assert proposal.task_type == 'LEAVE_REQUEST'
        assert proposal.status == 'ACTIVE'
        assert proposal.task_state == {'waiting_for': 'date'}
        assert proposal.summary == '等待用户补充请假日期'

    def test_parse_complete_proposal(self, extractor):
        proposal = extractor.parse_proposal(
            '{"action": "COMPLETE", "status": "COMPLETED", "summary": "done"}'
        )
        assert proposal.action == 'COMPLETE'
        assert proposal.status == 'COMPLETED'

    def test_parse_abandon_proposal(self, extractor):
        proposal = extractor.parse_proposal(
            '{"action": "ABANDON", "status": "ABANDONED", "summary": "user cancelled"}'
        )
        assert proposal.action == 'ABANDON'
        assert proposal.status == 'ABANDONED'

    @pytest.mark.parametrize('bad', ['UPDATE', 'delete', 'insert', '', 'none', 'upsert'])
    def test_unknown_action_rejected(self, extractor, bad):
        with pytest.raises(MemoryExtractionParseError):
            extractor.parse_proposal(json.dumps({'action': bad}))

    @pytest.mark.parametrize('bad', ['LEAVE', 'BUSINESS', 'foo', 'GENERIC ', 'generic'])
    def test_unknown_task_type_schema_layer_relaxed_p1a(self, extractor, bad):
        """P1-A：MemoryProposal.task_type 字段放宽为 ``str``；schema 不做白名单 fail-loud。

        白名单校验下沉到 ``MemoryTaskTypePolicy.is_allowed``；
        本测试断言 parse_proposal 接受任意字符串（不做白名单 fail-loud）。
        Policy fail-loud 由 ``test_memory_p1a_task_type_policy.py::TestTaskTypeValidation``
        覆盖。
        """
        proposal = extractor.parse_proposal(json.dumps({
            'action': 'UPSERT', 'task_type': bad, 'status': 'ACTIVE',
            'task_state': {'k': 'v'},
        }))
        assert proposal.task_type == bad

    @pytest.mark.parametrize('bad', ['PENDING', 'IN_PROGRESS', 'DONE', '', 'active'])
    def test_unknown_status_rejected(self, extractor, bad):
        with pytest.raises(MemoryExtractionParseError):
            extractor.parse_proposal(json.dumps({
                'action': 'UPSERT', 'task_type': 'GENERIC', 'status': bad,
            }))

    def test_extra_field_rejected(self, extractor):
        with pytest.raises(MemoryExtractionParseError):
            extractor.parse_proposal(json.dumps({
                'action': 'UPSERT', 'task_type': 'GENERIC', 'status': 'ACTIVE',
                'employee_id': 'E10001',  # trusted 字段不允许
            }))

    def test_summary_too_long_rejected(self, extractor):
        with pytest.raises(MemoryExtractionParseError):
            extractor.parse_proposal(json.dumps({
                'action': 'UPSERT', 'task_type': 'GENERIC', 'status': 'ACTIVE',
                'summary': 'x' * (MEMORY_PROPOSAL_SUMMARY_MAX_CHARS + 1),
            }))

    def test_task_state_string_json_rejected(self, extractor):
        with pytest.raises(MemoryExtractionParseError):
            extractor.parse_proposal(json.dumps({
                'action': 'UPSERT', 'task_type': 'GENERIC', 'status': 'ACTIVE',
                'task_state': '{"waiting_for": "date"}',  # 字符串不被接受
            }))

    def test_invalid_json_rejected(self, extractor):
        with pytest.raises(MemoryExtractionParseError, match='JSON 解析失败'):
            extractor.parse_proposal('{not valid json')

    def test_non_dict_payload_rejected(self, extractor):
        with pytest.raises(MemoryExtractionParseError):
            extractor.parse_proposal(json.dumps(['array', 'not', 'allowed']))
        with pytest.raises(MemoryExtractionParseError):
            extractor.parse_proposal(json.dumps('just a string'))
        with pytest.raises(MemoryExtractionParseError):
            extractor.parse_proposal(json.dumps(None))

    def test_non_string_input_rejected(self, extractor):
        with pytest.raises(MemoryExtractionParseError):
            extractor.parse_proposal(None)  # type: ignore[arg-type]
        with pytest.raises(MemoryExtractionParseError):
            extractor.parse_proposal(12345)  # type: ignore[arg-type]
        with pytest.raises(MemoryExtractionParseError):
            extractor.parse_proposal({'action': 'NONE'})  # type: ignore[arg-type]


# ---------- Extract 接口 ----------

class TestExtractShell:
    def test_extract_without_llm_callable_raises(self, extractor, basic_input):
        with pytest.raises(NotImplementedError):
            extractor.extract(basic_input)

    def test_extract_with_llm_callable_composes_build_and_parse(self, extractor, basic_input):
        captured = {}

        def fake_llm(system_prompt, user_prompt):
            captured['system'] = system_prompt
            captured['user'] = user_prompt
            return json.dumps({
                'action': 'UPSERT',
                'task_type': 'LEAVE_REQUEST',
                'status': 'ACTIVE',
                'task_state': {'waiting_for': 'date'},
                'summary': '等待用户补充请假日期',
            }, ensure_ascii=False)

        proposal = extractor.extract(basic_input, llm_callable=fake_llm)
        assert proposal.action == 'UPSERT'
        assert proposal.task_type == 'LEAVE_REQUEST'
        # 验证 llm_callable 接收到的 prompt 满足契约：
        # P1-A 起，extractor.system_prompt 是渲染后的字符串（默认 policy 下）。
        assert captured['system'] == extractor.system_prompt
        assert captured['user'] == extractor.build_prompt(basic_input)

    def test_extract_propagates_parse_error(self, extractor, basic_input):
        def fake_llm(system, user):
            return '{invalid json'

        with pytest.raises(MemoryExtractionParseError):
            extractor.extract(basic_input, llm_callable=fake_llm)