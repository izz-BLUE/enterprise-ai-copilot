from unittest.mock import patch

import pytest

from app.retrieval.query_rewriter import normalize_retrieval_query
from app.services.rag_answer_service import answer_rag


@pytest.mark.parametrize(
    ('original', 'expected'),
    [
        ('年假咋请？', '年假如何申请？'),
        ('病假怎么请？', '病假如何申请？'),
        ('事假咋申请？', '事假如何申请？'),
    ],
)
def test_normalize_short_colloquial_leave_apply_phrases(original, expected):
    assert normalize_retrieval_query(original) == expected


@pytest.mark.parametrize(
    'query',
    [
        '年假如何申请？',
        '年假有多少天？',
        '公司上班时间？',
        '年假余额是多少？',
        '帮我请明天一天年假',
        '怎么请假？',
        '怎么请示领导？',
        '怎么请教同事？',
        '咋请教一下？',
        '怎么请人帮忙？',
        '怎么请客户吃饭？',
    ],
)
def test_normalize_does_not_expand_unmatched_or_broader_queries(query):
    assert normalize_retrieval_query(query) == query


@patch('app.services.rag_answer_service.log_gate_event')
@patch('app.services.rag_answer_service.call_llm', return_value='answer')
@patch('app.services.rag_answer_service.build_rag_prompt', return_value='prompt')
@patch(
    'app.services.rag_answer_service.evaluate_gate_timed_fail_open',
    return_value=(None, 0.0),
)
@patch('app.services.rag_answer_service.retrieve_with_signals')
def test_production_entrypoint_normalizes_only_retrieval_query(
    retrieve, _gate, build_prompt, _call_llm, _log_gate,
):
    retrieve.return_value = (
        [{
            'id': 'chunk-1',
            'domain': 'hr',
            'source_file': 'leave.md',
            'chunk_index': 1,
            'content': 'context',
        }],
        [],
    )

    result = answer_rag('年假咋请？', trace_id='trace-normalization')

    retrieve.assert_called_once_with(
        '年假如何申请？',
        top_k=3,
        mode='hybrid',
    )
    build_prompt.assert_called_once()
    assert build_prompt.call_args.args[0] == '年假咋请？'
    assert result.answer == 'answer'


@patch('app.services.rag_answer_service.log_gate_event')
@patch('app.services.rag_answer_service.call_llm', return_value='answer')
@patch('app.services.rag_answer_service.build_rag_prompt', return_value='prompt')
@patch(
    'app.services.rag_answer_service.evaluate_gate_timed_fail_open',
    return_value=(None, 0.0),
)
@patch('app.services.rag_answer_service.retrieve_with_signals')
def test_explicit_retrieval_query_is_normalized_for_tool_entrypoint(
    retrieve, _gate, build_prompt, _call_llm, _log_gate,
):
    retrieve.return_value = (
        [{
            'id': 'chunk-1',
            'domain': 'hr',
            'source_file': 'leave.md',
            'chunk_index': 1,
            'content': 'context',
        }],
        [],
    )

    answer_rag(
        '年假咋请？',
        trace_id='trace-tool-normalization',
        retrieval_query='年假咋请？',
    )

    retrieve.assert_called_once_with(
        '年假如何申请？',
        top_k=3,
        mode='hybrid',
    )
    assert build_prompt.call_args.args[0] == '年假咋请？'


def test_agent_rag_chain_normalizes_retrieval_only_and_preserves_state():
    from app.agents.langgraph_agent import run_langgraph_agent

    chunks = [{
        'id': 'chunk-1',
        'domain': 'hr',
        'source_file': 'leave.md',
        'chunk_index': 1,
        'content': 'context',
    }]
    with patch(
        'app.services.rag_answer_service.retrieve_with_signals',
        return_value=(chunks, []),
    ) as retrieve, patch(
        'app.services.rag_answer_service.evaluate_gate_timed_fail_open',
        return_value=(None, 0.0),
    ), patch(
        'app.services.rag_answer_service.build_rag_prompt',
        return_value='prompt',
    ) as build_prompt, patch(
        'app.services.rag_answer_service.call_llm',
        return_value='answer',
    ), patch(
        'app.services.rag_answer_service.log_gate_event',
    ), patch(
        'app.agents.langgraph_agent.plan_annual_leave_action',
    ) as action_planner:
        result = run_langgraph_agent('年假咋请？', trace_id='trace-agent-normalization')

    retrieve.assert_called_once_with(
        '年假如何申请？',
        top_k=3,
        mode='hybrid',
    )
    assert build_prompt.call_args.args[0] == '年假咋请？'
    assert result['question'] == '年假咋请？'
    assert result['route'] == 'rag'
    action_planner.assert_not_called()
