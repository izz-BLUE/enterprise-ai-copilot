"""Model Reliability P0 行为回归测试：仅验证应用层 empty-only retry 行为。

普通 RAG:
1. empty → valid：call_llm 调用 2 次，最终返回 valid 内容（success=True）。
2. empty → empty：call_llm 调用 2 次，最终走现有失败兜底（success=False + 兜底文案）。
3. LLMProviderError：不触发应用层第二次调用，直接走兜底。

LangChain RAG Chain:
4. empty → valid：chain.invoke 调用 2 次，最终 success=True。
5. empty → empty：chain.invoke 调用 2 次后按现有失败契约返回（success=False）。

仅测试行为，不改生产实现；不打 Real Eval。
"""

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.services.llm_service import LLMProviderError


CHUNKS = [{
    'id': 'c1', 'domain': 'hr', 'source_file': 'sample.md',
    'chunk_index': 0, 'content': 'context',
}]
EMPTY_SIGNALS = []
NORMAL_SIGNALS = []


def _safety_ok():
    return {
        'safe': True, 'category': 'normal', 'reason': '', 'message': '',
    }


def _rewrite_ok():
    return {
        'rewritten_query': 'q', 'rewrite_applied': False, 'rewrite_reason': '',
    }


class TestRoutineRagRetry:
    """普通 RAG（process_chat）的 empty-only retry 行为。"""

    @patch('app.services.rag_service.log_gate_event')
    @patch('app.services.rag_service.call_llm')
    @patch('app.services.rag_service.build_rag_prompt', return_value='prompt')
    @patch(
        'app.services.rag_service.retrieve_with_signals',
        return_value=(CHUNKS, NORMAL_SIGNALS),
    )
    @patch('app.services.rag_service.rewrite_query', return_value=_rewrite_ok())
    @patch(
        'app.services.rag_service.check_user_query_safety',
        return_value=_safety_ok(),
    )
    def test_empty_then_valid_calls_twice_and_succeeds(
        self, _safety, _rewrite, _retrieve, _prompt, call_llm, _log_event,
    ):
        from app.services.rag_service import process_chat

        call_llm.side_effect = ['', 'recovered answer']

        response = process_chat('question', trace_id='trace-empty-valid')

        assert response.success is True
        assert response.answer == 'recovered answer'
        assert call_llm.call_count == 2

    @patch('app.services.rag_service.log_gate_event')
    @patch('app.services.rag_service.call_llm')
    @patch('app.services.rag_service.build_rag_prompt', return_value='prompt')
    @patch(
        'app.services.rag_service.retrieve_with_signals',
        return_value=(CHUNKS, NORMAL_SIGNALS),
    )
    @patch('app.services.rag_service.rewrite_query', return_value=_rewrite_ok())
    @patch(
        'app.services.rag_service.check_user_query_safety',
        return_value=_safety_ok(),
    )
    def test_empty_then_empty_calls_twice_then_fails(
        self, _safety, _rewrite, _retrieve, _prompt, call_llm, _log_event,
    ):
        from app.services.rag_service import process_chat

        call_llm.side_effect = ['', '']

        response = process_chat('question', trace_id='trace-empty-empty')

        # 现有失败兜底：success=False + 兜底文案
        assert response.success is False
        assert '暂时不可用' in response.answer
        assert call_llm.call_count == 2

    @patch('app.services.rag_service.log_gate_event')
    @patch('app.services.rag_service.call_llm')
    @patch('app.services.rag_service.build_rag_prompt', return_value='prompt')
    @patch(
        'app.services.rag_service.retrieve_with_signals',
        return_value=(CHUNKS, NORMAL_SIGNALS),
    )
    @patch('app.services.rag_service.rewrite_query', return_value=_rewrite_ok())
    @patch(
        'app.services.rag_service.check_user_query_safety',
        return_value=_safety_ok(),
    )
    def test_provider_error_does_not_trigger_app_retry(
        self, _safety, _rewrite, _retrieve, _prompt, call_llm, _log_event,
    ):
        from app.services.rag_service import process_chat

        call_llm.side_effect = LLMProviderError(
            'provider_timeout', 'LLM 调用超时',
        )

        response = process_chat('question', trace_id='trace-provider-error')

        assert response.success is False
        assert '暂时不可用' in response.answer
        # 应用层不触发第二次 LLM 调用
        assert call_llm.call_count == 1


class TestLangChainRagRetry:
    """LangChain RAG Chain 的 empty-only retry 行为。"""

    def _make_response(self, content):
        # 使用简单对象而非 MagicMock，避免 getattr 触发动态属性回退成 Mock 实例。
        class _Resp:
            def __init__(self, content):
                self.content = content
        return _Resp(content)

    def test_empty_then_valid_invokes_twice_and_succeeds(self):
        from app.chains import langchain_rag_chain as module

        invoke = MagicMock(side_effect=[
            self._make_response(''),
            self._make_response('recovered langgraph answer'),
        ])
        # 仿照 test_shadow_generation_paths.py 的 _FakePrompt 写法（__or__）。
        class _FakePrompt:
            def __init__(self, invoke_mock):
                self.invoke_mock = invoke_mock
            def __or__(self, _llm):
                return type('FakeChain', (), {'invoke': self.invoke_mock})()
        fake_prompt = _FakePrompt(invoke)

        with patch.object(module.ChatPromptTemplate, 'from_messages',
                          return_value=fake_prompt), \
             patch.object(module, 'ChatOpenAI', return_value=object()), \
             patch('app.chains.langchain_rag_chain.log_gate_event'), \
             patch(
                 'app.chains.langchain_rag_chain.retrieve_with_signals',
                 return_value=(CHUNKS, NORMAL_SIGNALS),
             ):
            result = module.answer_with_langchain_rag(
                'original question', retrieval_query='q',
                trace_id='trace-lc-empty-valid',
            )

        assert result['success'] is True
        assert result['answer'] == 'recovered langgraph answer'
        assert invoke.call_count == 2

    def test_empty_then_empty_invokes_twice_then_fails(self):
        from app.chains import langchain_rag_chain as module

        invoke = MagicMock(side_effect=[
            self._make_response(''),
            self._make_response(''),
        ])
        class _FakePrompt:
            def __init__(self, invoke_mock):
                self.invoke_mock = invoke_mock
            def __or__(self, _llm):
                return type('FakeChain', (), {'invoke': self.invoke_mock})()
        fake_prompt = _FakePrompt(invoke)

        with patch.object(module.ChatPromptTemplate, 'from_messages',
                          return_value=fake_prompt), \
             patch.object(module, 'ChatOpenAI', return_value=object()), \
             patch('app.chains.langchain_rag_chain.log_gate_event'), \
             patch(
                 'app.chains.langchain_rag_chain.retrieve_with_signals',
                 return_value=(CHUNKS, NORMAL_SIGNALS),
             ):
            result = module.answer_with_langchain_rag(
                'original question', retrieval_query='q',
                trace_id='trace-lc-empty-empty',
            )

        # 现有失败契约：success=False + 兜底文案
        assert result['success'] is False
        assert '暂时不可用' in result['answer']
        assert invoke.call_count == 2