"""test_memory_llm_adapter.py —— MemoryLLMAdapter 测试

覆盖：

Success / 形态：
  1. callable 形态 client：返回 JSON string → adapter 原样返回
  2. .call() 方法 client：返回 JSON string → adapter 原样返回
  3. system_prompt / user_prompt 原样透传（不修改 / 不拼接）

Empty result：
  4. None 响应 → MemoryLLMAdapterEmptyResponseError
  5. 空字符串响应 → MemoryLLMAdapterEmptyResponseError
  6. 纯空白响应 → MemoryLLMAdapterEmptyResponseError
  7. 非字符串返回（如 int） → MemoryLLMAdapterEmptyResponseError

Client 异常：
  8. client 抛 RuntimeError → 原样向上传播
  9. client 抛 LLMProviderError 类异常 → 原样向上传播（不包装）
 10. client 抛 ValueError → 原样向上传播

契约：
 11. llm_client=None → MemoryLLMAdapterError
 12. 既不可调用又无 .call 方法的对象 → MemoryLLMAdapterError
 13. MemoryLLMAdapterEmptyResponseError 是 MemoryLLMAdapterError 子类
 14. MemoryLLMAdapterError 是 RuntimeError 子类
 15. 与 MemoryExtractor 集成：build_prompt + adapter + parse_proposal 完整链路
"""

import json

import pytest

from app.memory.memory_extractor import MemoryExtractor
from app.memory.memory_llm_adapter import (
    MemoryLLMAdapter,
    MemoryLLMAdapterEmptyResponseError,
    MemoryLLMAdapterError,
)
from app.schemas.memory_schema import MemoryExtractionInput

SYSTEM_PROMPT = 'SYSTEM_PROMPT_SENTINEL'
USER_PROMPT = 'USER_PROMPT_SENTINEL'


# ---------- Success / 形态 ----------

class TestCallableForm:
    def test_callable_client_returns_string_verbatim(self):
        def fake_client(system, user):
            return '{"action": "NONE"}'

        adapter = MemoryLLMAdapter(fake_client)
        result = adapter(SYSTEM_PROMPT, USER_PROMPT)
        assert result == '{"action": "NONE"}'

    def test_lambda_also_supported(self):
        adapter = MemoryLLMAdapter(lambda s, u: '{"action":"NONE"}')
        assert adapter(SYSTEM_PROMPT, USER_PROMPT) == '{"action":"NONE"}'


class TestCallMethodForm:
    class _FakeClient:
        """具有 .call() 方法的对象形态。"""

        def __init__(self, response):
            self._response = response

        def call(self, system_prompt, user_prompt):
            return self._response

    def test_call_method_client(self):
        client = self._FakeClient('{"action": "NONE"}')
        adapter = MemoryLLMAdapter(client)
        result = adapter(SYSTEM_PROMPT, USER_PROMPT)
        assert result == '{"action": "NONE"}'

    def test_call_method_with_callable_priority_over_callable(self):
        """当对象同时有 .call 与 __call__ 时，优先用 .call（duck typing 明确）。"""
        class _BothClient:
            def __call__(self, system, user):
                return 'via __call__'

            def call(self, system, user):
                return 'via .call()'

        adapter = MemoryLLMAdapter(_BothClient())
        # 由于 hasattr('call') 命中 .call 优先
        assert adapter(SYSTEM_PROMPT, USER_PROMPT) == 'via .call()'


class TestPromptPassthrough:
    def test_prompts_passed_through_verbatim(self):
        """system_prompt / user_prompt 必须原样传入 client，不做任何修改。"""
        captured = {}

        def fake_client(system, user):
            captured['system'] = system
            captured['user'] = user
            return '{"action":"NONE"}'

        adapter = MemoryLLMAdapter(fake_client)
        adapter(SYSTEM_PROMPT, USER_PROMPT)
        assert captured['system'] == SYSTEM_PROMPT
        assert captured['user'] == USER_PROMPT

    def test_prompts_passed_through_verbatim_call_method_form(self):
        captured = {}

        class _Client:
            def call(self, system, user):
                captured['system'] = system
                captured['user'] = user
                return '{"action":"NONE"}'

        adapter = MemoryLLMAdapter(_Client())
        adapter(SYSTEM_PROMPT, USER_PROMPT)
        assert captured['system'] == SYSTEM_PROMPT
        assert captured['user'] == USER_PROMPT

    def test_does_not_inject_identity(self):
        """Adapter 不得拼接 conversation_id / employee_id / user_id 等身份信息。"""
        captured = {}

        def fake_client(system, user):
            captured['system'] = system
            captured['user'] = user
            return '{"action":"NONE"}'

        adapter = MemoryLLMAdapter(fake_client)
        adapter(SYSTEM_PROMPT, USER_PROMPT)
        for forbidden in (
            'conversationId', 'conversation_id', 'employee_id', 'user_id',
            'allow_eval', 'allow_business_actions', 'business_date',
        ):
            assert forbidden not in captured['system']
            assert forbidden not in captured['user']


# ---------- Empty result ----------

class TestEmptyResponse:
    def test_none_response_raises(self):
        def fake_client(system, user):
            return None

        adapter = MemoryLLMAdapter(fake_client)
        with pytest.raises(MemoryLLMAdapterEmptyResponseError, match='返回 None'):
            adapter(SYSTEM_PROMPT, USER_PROMPT)

    def test_empty_string_raises(self):
        adapter = MemoryLLMAdapter(lambda s, u: '')
        with pytest.raises(MemoryLLMAdapterEmptyResponseError, match='空字符串'):
            adapter(SYSTEM_PROMPT, USER_PROMPT)

    def test_whitespace_only_raises(self):
        adapter = MemoryLLMAdapter(lambda s, u: '   \n\t  ')
        with pytest.raises(MemoryLLMAdapterEmptyResponseError, match='空白'):
            adapter(SYSTEM_PROMPT, USER_PROMPT)

    def test_non_string_response_raises(self):
        adapter = MemoryLLMAdapter(lambda s, u: 12345)
        with pytest.raises(MemoryLLMAdapterEmptyResponseError, match='非字符串'):
            adapter(SYSTEM_PROMPT, USER_PROMPT)

    def test_empty_response_call_method_form(self):
        class _Client:
            def call(self, system, user):
                return None

        adapter = MemoryLLMAdapter(_Client())
        with pytest.raises(MemoryLLMAdapterEmptyResponseError):
            adapter(SYSTEM_PROMPT, USER_PROMPT)


# ---------- Client 异常 ----------

class TestClientExceptionPropagation:
    def test_runtime_error_propagates(self):
        def fake_client(system, user):
            raise RuntimeError('boom')

        adapter = MemoryLLMAdapter(fake_client)
        with pytest.raises(RuntimeError, match='boom'):
            adapter(SYSTEM_PROMPT, USER_PROMPT)

    def test_value_error_propagates(self):
        adapter = MemoryLLMAdapter(lambda s, u: (_ for _ in ()).throw(ValueError('bad')))
        with pytest.raises(ValueError, match='bad'):
            adapter(SYSTEM_PROMPT, USER_PROMPT)

    def test_custom_llm_exception_propagates_unchanged(self):
        """模拟 LLMProviderError 风格的客户端异常：原样上抛，adapter 不包装。"""

        class _LLMError(Exception):
            def __init__(self, code, message):
                super().__init__(message)
                self.code = code

        def fake_client(system, user):
            raise _LLMError('provider_timeout', 'timed out')

        adapter = MemoryLLMAdapter(fake_client)
        with pytest.raises(_LLMError) as exc_info:
            adapter(SYSTEM_PROMPT, USER_PROMPT)
        assert exc_info.value.code == 'provider_timeout'
        assert isinstance(exc_info.value, _LLMError)  # 仍然是 _LLMError，不是被包装

    def test_call_method_exception_propagates(self):
        class _Client:
            def call(self, system, user):
                raise RuntimeError('call_method_failed')

        adapter = MemoryLLMAdapter(_Client())
        with pytest.raises(RuntimeError, match='call_method_failed'):
            adapter(SYSTEM_PROMPT, USER_PROMPT)


# ---------- 契约 ----------

class TestAdapterContract:
    def test_none_client_raises(self):
        with pytest.raises(MemoryLLMAdapterError, match='不能为空'):
            MemoryLLMAdapter(None)

    def test_non_callable_client_raises(self):
        class _NotCallable:
            pass

        with pytest.raises(MemoryLLMAdapterError, match='必须'):
            MemoryLLMAdapter(_NotCallable())

    def test_empty_response_error_inherits_adapter_error(self):
        assert issubclass(MemoryLLMAdapterEmptyResponseError, MemoryLLMAdapterError)

    def test_adapter_error_inherits_runtime_error(self):
        assert issubclass(MemoryLLMAdapterError, RuntimeError)


# ---------- 与 MemoryExtractor 集成 ----------

class TestExtractorIntegration:
    def test_full_pipeline_build_prompt_adapter_parse(self):
        """MemoryExtractor + MemoryLLMAdapter 完整链路：build_prompt → LLM → parse_proposal。"""
        captured = {}

        def fake_client(system_prompt, user_prompt):
            captured['system'] = system_prompt
            captured['user'] = user_prompt
            return json.dumps({
                'action': 'UPSERT',
                'task_type': 'LEAVE_REQUEST',
                'status': 'ACTIVE',
                'task_state': {'waiting_for': 'date'},
                'summary': '等待用户补充请假日期',
            }, ensure_ascii=False)

        extractor = MemoryExtractor()
        adapter = MemoryLLMAdapter(fake_client)
        inp = MemoryExtractionInput(
            question='我想请假',
            tool_history=[{
                'tool_name': 'rag_answer_tool',
                'arguments': {'question': '年假'},
                'status': 'success',
                'observation': 'ok',
            }],
        )
        proposal = extractor.extract(inp, llm_callable=adapter)
        # 验证 adapter 把 extractor 的 system_prompt / build_prompt 输出原样传给 client。
        # P1-A：system prompt 是渲染后的字符串（默认 policy），不再等于模板常量。
        assert captured['system'] == extractor.system_prompt
        assert '当前事实信息' in captured['user']
        assert '我想请假' in captured['user']
        # 验证最终 proposal
        assert proposal.action == 'UPSERT'
        assert proposal.task_type == 'LEAVE_REQUEST'
        assert proposal.task_state == {'waiting_for': 'date'}

    def test_full_pipeline_empty_llm_response_fails_at_parse(self):
        """Adapter 空响应会冒到 Extractor.parse_proposal；不再静默。"""

        def fake_client(system_prompt, user_prompt):
            return ''  # 空响应

        extractor = MemoryExtractor()
        adapter = MemoryLLMAdapter(fake_client)
        inp = MemoryExtractionInput(question='hi')
        with pytest.raises(MemoryLLMAdapterEmptyResponseError):
            # adapter 在被调用阶段即抛错
            extractor.extract(inp, llm_callable=adapter)