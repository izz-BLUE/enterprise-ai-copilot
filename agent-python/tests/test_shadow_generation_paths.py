from unittest.mock import patch

from app.retrieval.retrieval_gate import (
    CandidateSignals,
    get_gate_metrics,
    reset_gate_metrics,
)
from app.services.rag_answer_service import answer_rag

BLOCKED_SIGNALS = [CandidateSignals('no-answer', vector_score=0.60, bm25_score=2.09)]
CHUNKS = [{
    'id': 'no-answer', 'domain': 'hr', 'source_file': 'sample.md',
    'chunk_index': 0, 'content': 'irrelevant context',
}]


def _common_patches():
    return (
        patch('app.services.rag_answer_service.retrieve_with_signals', return_value=(CHUNKS, BLOCKED_SIGNALS)),
        patch('app.services.rag_answer_service.build_rag_prompt', return_value='prompt'),
        patch('app.services.rag_answer_service.log_gate_event'),
    )


def test_fixed_off_gate_still_calls_llm():
    retrieve, prompt, log_event = _common_patches()
    with retrieve, prompt, log_event as logged, \
            patch('app.services.rag_answer_service.call_llm', return_value='answer') as llm:
        result = answer_rag('question', trace_id='trace-off', retrieval_query='query')

    assert result.success is True
    llm.assert_called_once()
    assert logged.call_args.kwargs['llm_called'] is True
    assert logged.call_args.kwargs['decision'].answerable is True


def test_fixed_off_gate_does_not_block_llm():
    retrieve, prompt, log_event = _common_patches()
    with retrieve, prompt, log_event as logged, \
            patch('app.services.rag_answer_service.call_llm', return_value='answer') as llm:
        result = answer_rag('question', trace_id='trace-off', retrieval_query='query')

    assert result.success is True
    assert result.answer == 'answer'
    llm.assert_called_once()
    assert logged.call_args.kwargs['llm_called'] is True


def test_gate_error_remains_fail_open_and_calls_llm():
    retrieve, prompt, log_event = _common_patches()
    reset_gate_metrics()
    with retrieve, prompt, log_event as logged, \
            patch('app.services.rag_answer_service.call_llm', return_value='answer') as llm, \
            patch('app.retrieval.retrieval_gate.evaluate_gate', side_effect=RuntimeError('gate failed')):
        result = answer_rag('question', trace_id='trace-error', retrieval_query='query')

    assert result.success is True
    llm.assert_called_once()
    assert get_gate_metrics()['gate_evaluation_error'] == 1
    assert logged.call_args.kwargs['decision'].mode_reason_code == 'gate_disabled'


def test_gate_error_never_blocks_generation():
    retrieve, prompt, log_event = _common_patches()
    with retrieve, prompt, log_event as logged, \
            patch('app.services.rag_answer_service.call_llm', return_value='answer') as llm, \
            patch('app.retrieval.retrieval_gate.evaluate_gate', side_effect=RuntimeError('gate failed')):
        result = answer_rag('question', trace_id='trace-error', retrieval_query='query')

    assert result.answer == 'answer'
    llm.assert_called_once()
    assert logged.call_args.kwargs['decision'].mode_reason_code == 'gate_disabled'


def test_retrieval_error_does_not_call_llm():
    with patch(
        'app.services.rag_answer_service.retrieve_with_signals',
        side_effect=RuntimeError('retrieval failed'),
    ), patch('app.services.rag_answer_service.call_llm') as llm:
        result = answer_rag('question', trace_id='trace-retrieval', retrieval_query='query')

    assert result.success is False
    llm.assert_not_called()


def test_empty_candidates_return_without_llm():
    with patch('app.services.rag_answer_service.retrieve_with_signals', return_value=([], [])), \
            patch('app.services.rag_answer_service.log_gate_event') as logged, \
            patch('app.services.rag_answer_service.call_llm') as llm:
        result = answer_rag('question', trace_id='trace-empty', retrieval_query='query')

    assert result.answer == '当前知识库暂无相关信息'
    assert result.sources == []
    llm.assert_not_called()
    assert logged.call_args.kwargs['llm_called'] is False
