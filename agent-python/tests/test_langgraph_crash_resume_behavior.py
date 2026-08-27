"""Minimal Phase A proof for the locked LangGraph 1.2.9 behavior."""

from typing import TypedDict

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph


class _Context(TypedDict):
    request_token: str


class _State(TypedDict):
    events: list[str]
    context_token: str


def _config(thread_id: str) -> dict:
    return {'configurable': {'thread_id': thread_id}}


def test_langgraph_exception_leaves_pending_checkpoint_and_invoke_none_resumes():
    calls = {'completed': 0, 'failing': 0}
    fail_first_attempt = {'value': True}

    def completed_node(state: _State) -> dict:
        calls['completed'] += 1
        return {'events': [*state['events'], 'completed']}

    def failing_node(state: _State, runtime) -> dict:
        calls['failing'] += 1
        if fail_first_attempt['value']:
            fail_first_attempt['value'] = False
            raise RuntimeError('phase-a simulated graph failure')
        return {
            'events': [*state['events'], 'resumed'],
            'context_token': runtime.context['request_token'],
        }

    def final_node(state: _State) -> dict:
        return {'events': [*state['events'], 'final']}

    builder = StateGraph(_State, context_schema=_Context)
    builder.add_node('completed_node', completed_node)
    builder.add_node('failing_node', failing_node)
    builder.add_node('final_node', final_node)
    builder.add_edge(START, 'completed_node')
    builder.add_edge('completed_node', 'failing_node')
    builder.add_edge('failing_node', 'final_node')
    builder.add_edge('final_node', END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = _config('phase-a-crash-resume')

    with pytest.raises(RuntimeError, match='phase-a simulated graph failure'):
        graph.invoke(
            {'events': [], 'context_token': ''},
            config=config,
            context={'request_token': 'trace-a'},
            durability='sync',
        )

    snapshot = graph.get_state(config)
    assert type(snapshot).__name__ == 'StateSnapshot'
    assert snapshot.next == ('failing_node',)
    assert snapshot.tasks
    assert calls['completed'] == 1
    assert snapshot.values['events'] == ['completed']

    result = graph.invoke(
        None,
        config=config,
        context={'request_token': 'trace-b'},
        durability='sync',
    )

    assert result['events'] == ['completed', 'resumed', 'final']
    assert result['context_token'] == 'trace-b'
    assert calls['completed'] == 1
    assert calls['failing'] == 2
    assert graph.get_state(config).next == ()


def test_langgraph_completed_checkpoint_has_empty_next():
    builder = StateGraph(_State)
    builder.add_node('done', lambda state: {'events': [*state['events'], 'done']})
    builder.add_edge(START, 'done')
    builder.add_edge('done', END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = _config('phase-a-complete')

    graph.invoke({'events': [], 'context_token': ''}, config=config, durability='sync')

    snapshot = graph.get_state(config)
    assert type(snapshot).__name__ == 'StateSnapshot'
    assert snapshot.next == ()
