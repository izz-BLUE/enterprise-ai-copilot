"""RAG/LLM 可靠性回归：统一入口、显式单次尝试和稳定失败契约。"""

from unittest.mock import patch

from app.services.llm_service import LLMProviderError
from app.services.rag_answer_service import RagAnswerResult, answer_rag

CHUNKS = [{
    'id': 'c1', 'domain': 'hr', 'source_file': 'sample.md',
    'chunk_index': 0, 'content': 'context',
}]


def _rewrite_ok():
    return {
        'rewritten_query': 'q', 'rewrite_applied': False, 'rewrite_reason': '',
    }


@patch('app.services.rag_answer_service.log_gate_event')
@patch('app.services.rag_answer_service.build_rag_prompt', return_value='prompt')
@patch('app.services.rag_answer_service.retrieve_with_signals', return_value=(CHUNKS, []))
@patch('app.services.rag_answer_service.rewrite_query', return_value=_rewrite_ok())
def test_empty_response_is_not_retried(
    _rewrite, _retrieve, _prompt, _log_event,
):
    with patch('app.services.rag_answer_service.call_llm', return_value='') as call_llm:
        result = answer_rag('question', trace_id='trace-empty')

    assert result.success is False
    assert '暂时不可用' in result.answer
    call_llm.assert_called_once()


@patch('app.services.rag_answer_service.log_gate_event')
@patch('app.services.rag_answer_service.build_rag_prompt', return_value='prompt')
@patch('app.services.rag_answer_service.retrieve_with_signals', return_value=(CHUNKS, []))
@patch('app.services.rag_answer_service.rewrite_query', return_value=_rewrite_ok())
def test_valid_response_returns_traceable_sources(
    _rewrite, _retrieve, _prompt, _log_event,
):
    with patch('app.services.rag_answer_service.call_llm', return_value='answer') as call_llm:
        result = answer_rag('question', trace_id='trace-valid')

    assert result.success is True
    assert result.answer == 'answer'
    assert result.sources == ['hr/sample.md#chunk-0']
    call_llm.assert_called_once()


@patch('app.services.rag_answer_service.log_gate_event')
@patch('app.services.rag_answer_service.build_rag_prompt', return_value='prompt')
@patch('app.services.rag_answer_service.retrieve_with_signals', return_value=(CHUNKS, []))
@patch('app.services.rag_answer_service.rewrite_query', return_value=_rewrite_ok())
def test_provider_error_does_not_trigger_application_retry(
    _rewrite, _retrieve, _prompt, _log_event,
):
    error = LLMProviderError('provider_timeout', 'LLM 调用超时')
    with patch('app.services.rag_answer_service.call_llm', side_effect=error) as call_llm:
        result = answer_rag('question', trace_id='trace-provider-error')

    assert result.success is False
    assert '暂时不可用' in result.answer
    call_llm.assert_called_once()


def test_legacy_langchain_entrypoint_delegates_to_unified_service():
    from app.chains import langchain_rag_chain as module

    expected = RagAnswerResult('answer', 'model', True, ['hr/sample.md#chunk-0'])
    with patch.object(module, 'answer_rag', return_value=expected) as unified:
        result = module.answer_with_langchain_rag(
            'original', retrieval_query='rewritten', trace_id='trace-wrapper',
        )

    unified.assert_called_once_with(
        'original', trace_id='trace-wrapper', top_k=3, retrieval_query='rewritten',
    )
    assert result == {
        'answer': 'answer',
        'model': 'model',
        'success': True,
        'sources': ['hr/sample.md#chunk-0'],
    }
