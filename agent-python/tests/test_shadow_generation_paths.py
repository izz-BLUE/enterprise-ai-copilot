import os
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.retrieval.retrieval_gate import (
    CandidateSignals,
    evaluate_gate,
    get_gate_metrics,
    reset_gate_metrics,
)


BLOCKED_SIGNALS = [
    CandidateSignals('no-answer', vector_score=0.60, bm25_score=2.09),
]
CHUNKS = [{
    'id': 'no-answer', 'domain': 'hr', 'source_file': 'sample.md',
    'chunk_index': 0, 'content': 'irrelevant context',
}]


class _FakeChain:
    def __init__(self, invoke_mock):
        self.invoke = invoke_mock


class _FakePrompt:
    def __init__(self, invoke_mock):
        self.invoke_mock = invoke_mock

    def __or__(self, _llm):
        return _FakeChain(self.invoke_mock)


class ShadowGenerationPathsTest(unittest.TestCase):
    @patch('app.services.rag_service.log_gate_event')
    @patch('app.services.rag_service.call_llm', return_value='unchanged answer')
    @patch('app.services.rag_service.build_rag_prompt', return_value='prompt')
    @patch('app.services.rag_service.retrieve_with_signals', return_value=(CHUNKS, BLOCKED_SIGNALS))
    @patch('app.services.rag_service.rewrite_query', return_value={
        'rewritten_query': 'query', 'rewrite_applied': False, 'rewrite_reason': '',
    })
    @patch('app.services.rag_service.check_user_query_safety', return_value={
        'safe': True, 'category': 'normal', 'reason': '', 'message': '',
    })
    def test_shadow_block_still_calls_regular_llm(
        self, _safety, _rewrite, _retrieve, _prompt, call_llm, log_event,
    ):
        from app.services.rag_service import process_chat

        with patch('app.retrieval.retrieval_gate.RAG_GATE_MODE', 'shadow'):
            response = process_chat('question', trace_id='trace-test')

        self.assertTrue(response.success)
        self.assertEqual('unchanged answer', response.answer)
        call_llm.assert_called_once()
        self.assertTrue(log_event.call_args.kwargs['llm_called'])
        self.assertFalse(log_event.call_args.kwargs['decision'].answerable)

    @patch('app.chains.langchain_rag_chain.log_gate_event')
    @patch('app.chains.langchain_rag_chain.retrieve_with_signals', return_value=(CHUNKS, BLOCKED_SIGNALS))
    def test_shadow_block_still_calls_langgraph_llm(self, _retrieve, log_event):
        from app.chains import langchain_rag_chain as module

        invoke = Mock(return_value=Mock(content='unchanged langgraph answer'))
        fake_prompt = _FakePrompt(invoke)
        with patch.object(module.ChatPromptTemplate, 'from_messages', return_value=fake_prompt), \
             patch.object(module, 'ChatOpenAI', return_value=object()), \
             patch('app.retrieval.retrieval_gate.RAG_GATE_MODE', 'shadow'):
            result = module.answer_with_langchain_rag(
                'original question', retrieval_query='query', trace_id='trace-test',
            )

        self.assertTrue(result['success'])
        self.assertEqual('unchanged langgraph answer', result['answer'])
        invoke.assert_called_once()
        self.assertTrue(log_event.call_args.kwargs['llm_called'])
        self.assertFalse(log_event.call_args.kwargs['decision'].answerable)

    def test_same_signals_produce_same_decision_for_both_paths(self):
        regular = evaluate_gate(BLOCKED_SIGNALS, mode='shadow')
        langgraph = evaluate_gate(BLOCKED_SIGNALS, mode='shadow')
        self.assertEqual(regular, langgraph)

    @patch('app.services.rag_service.log_gate_event')
    @patch('app.services.rag_service.call_llm', return_value='original regular answer')
    @patch('app.services.rag_service.build_rag_prompt', return_value='prompt')
    @patch('app.services.rag_service.retrieve_with_signals', return_value=(CHUNKS, BLOCKED_SIGNALS))
    @patch('app.services.rag_service.rewrite_query', return_value={
        'rewritten_query': 'query', 'rewrite_applied': False, 'rewrite_reason': '',
    })
    @patch('app.services.rag_service.check_user_query_safety', return_value={
        'safe': True, 'category': 'normal', 'reason': '', 'message': '',
    })
    def test_regular_gate_error_fails_open_and_calls_llm(
        self, _safety, _rewrite, _retrieve, _prompt, call_llm, log_event,
    ):
        from app.services.rag_service import process_chat

        reset_gate_metrics()
        with patch(
            'app.retrieval.retrieval_gate.evaluate_gate',
            side_effect=RuntimeError('gate failed'),
        ), patch('app.retrieval.retrieval_gate.RAG_GATE_MODE', 'shadow'):
            response = process_chat('question', trace_id='trace-gate-error')

        self.assertTrue(response.success)
        self.assertEqual('original regular answer', response.answer)
        call_llm.assert_called_once()
        self.assertEqual(1, get_gate_metrics()['gate_evaluation_error'])
        self.assertEqual(
            'shadow_fail_open',
            log_event.call_args.kwargs['decision'].mode_reason_code,
        )

    @patch('app.chains.langchain_rag_chain.log_gate_event')
    @patch('app.chains.langchain_rag_chain.retrieve_with_signals', return_value=(CHUNKS, BLOCKED_SIGNALS))
    def test_langgraph_gate_error_fails_open_and_calls_llm(self, _retrieve, log_event):
        from app.chains import langchain_rag_chain as module

        reset_gate_metrics()
        invoke = Mock(return_value=Mock(content='original langgraph answer'))
        fake_prompt = _FakePrompt(invoke)
        with patch.object(module.ChatPromptTemplate, 'from_messages', return_value=fake_prompt), \
             patch.object(module, 'ChatOpenAI', return_value=object()), \
             patch('app.retrieval.retrieval_gate.evaluate_gate', side_effect=RuntimeError('gate failed')), \
             patch('app.retrieval.retrieval_gate.RAG_GATE_MODE', 'shadow'):
            result = module.answer_with_langchain_rag(
                'original question', retrieval_query='query', trace_id='trace-gate-error',
            )

        self.assertTrue(result['success'])
        self.assertEqual('original langgraph answer', result['answer'])
        self.assertEqual(['no-answer'], [source['id'] for source in result['sources']])
        invoke.assert_called_once()
        self.assertEqual(1, get_gate_metrics()['gate_evaluation_error'])
        self.assertEqual(
            'shadow_fail_open',
            log_event.call_args.kwargs['decision'].mode_reason_code,
        )

    @patch('app.services.rag_service.call_llm')
    @patch('app.services.rag_service.retrieve_with_signals', side_effect=RuntimeError('retrieval failed'))
    @patch('app.services.rag_service.rewrite_query', return_value={
        'rewritten_query': 'query', 'rewrite_applied': False, 'rewrite_reason': '',
    })
    @patch('app.services.rag_service.check_user_query_safety', return_value={
        'safe': True, 'category': 'normal', 'reason': '', 'message': '',
    })
    def test_retrieval_error_is_not_gate_fail_open(
        self, _safety, _rewrite, _retrieve, call_llm,
    ):
        from app.services.rag_service import process_chat

        reset_gate_metrics()
        response = process_chat('question', trace_id='trace-retrieval-error')
        self.assertFalse(response.success)
        call_llm.assert_not_called()
        self.assertEqual(0, get_gate_metrics()['gate_evaluation_error'])

    @patch('app.services.rag_service.log_gate_event')
    @patch('app.services.rag_service.call_llm', return_value='empty-context answer')
    @patch('app.services.rag_service.build_rag_prompt', return_value='empty prompt')
    @patch('app.services.rag_service.retrieve_with_signals', return_value=([], []))
    @patch('app.services.rag_service.rewrite_query', return_value={
        'rewritten_query': 'query', 'rewrite_applied': False, 'rewrite_reason': '',
    })
    @patch('app.services.rag_service.check_user_query_safety', return_value={
        'safe': True, 'category': 'normal', 'reason': '', 'message': '',
    })
    def test_regular_empty_candidates_preserve_original_llm_call(
        self, _safety, _rewrite, _retrieve, _prompt, call_llm, log_event,
    ):
        from app.services.rag_service import process_chat

        response = process_chat('question', trace_id='trace-empty')
        self.assertTrue(response.success)
        call_llm.assert_called_once()
        self.assertTrue(log_event.call_args.kwargs['llm_called'])

    @patch('app.chains.langchain_rag_chain.log_gate_event')
    @patch('app.chains.langchain_rag_chain.retrieve_with_signals', return_value=([], []))
    def test_langgraph_empty_candidates_preserve_original_early_return(self, _retrieve, log_event):
        from app.chains import langchain_rag_chain as module

        with patch.object(module, 'ChatOpenAI') as chat_open_ai:
            result = module.answer_with_langchain_rag(
                'question', retrieval_query='query', trace_id='trace-empty',
            )

        self.assertTrue(result['success'])
        self.assertEqual('当前知识库暂无相关信息', result['answer'])
        self.assertEqual([], result['sources'])
        chat_open_ai.assert_not_called()
        self.assertFalse(log_event.call_args.kwargs['llm_called'])


if __name__ == '__main__':
    unittest.main()
