"""Minimal Phase A proof for the locked LangGraph 1.2.9 behavior."""

from typing import TypedDict

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt


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


def test_langgraph_sequential_dynamic_interrupts_resume_one_at_a_time():
    class SequentialState(TypedDict, total=False):
        user_decision: str
        external_decision: str

    calls = {'user_approval': 0, 'external_approval': 0, 'final': 0}

    def user_approval_node(_state: SequentialState) -> dict:
        calls['user_approval'] += 1
        decision = interrupt({'kind': 'user_wait'})
        return {'user_decision': decision}

    def external_approval_node(_state: SequentialState) -> dict:
        calls['external_approval'] += 1
        decision = interrupt({'kind': 'external_wait'})
        return {'external_decision': decision}

    def final_node(_state: SequentialState) -> dict:
        calls['final'] += 1
        return {}

    builder = StateGraph(SequentialState)
    builder.add_node('user_approval_node', user_approval_node)
    builder.add_node('external_approval_node', external_approval_node)
    builder.add_node('final_node', final_node)
    builder.add_edge(START, 'user_approval_node')
    builder.add_edge('user_approval_node', 'external_approval_node')
    builder.add_edge('external_approval_node', 'final_node')
    builder.add_edge('final_node', END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = _config('phase-a-sequential-interrupts')

    first = graph.invoke({}, config=config, durability='sync')
    first_snapshot = graph.get_state(config)
    assert first['__interrupt__'][0].value == {'kind': 'user_wait'}
    assert first_snapshot.next == ('user_approval_node',)
    assert calls == {'user_approval': 1, 'external_approval': 0, 'final': 0}

    second = graph.invoke(Command(resume='CONFIRMED'), config=config, durability='sync')
    second_snapshot = graph.get_state(config)
    assert second['user_decision'] == 'CONFIRMED'
    assert second['__interrupt__'][0].value == {'kind': 'external_wait'}
    assert second_snapshot.next == ('external_approval_node',)
    assert calls == {'user_approval': 2, 'external_approval': 1, 'final': 0}

    final = graph.invoke(Command(resume='APPROVED'), config=config, durability='sync')
    final_snapshot = graph.get_state(config)
    assert final['user_decision'] == 'CONFIRMED'
    assert final['external_decision'] == 'APPROVED'
    assert final_snapshot.next == ()
    assert calls == {'user_approval': 2, 'external_approval': 2, 'final': 1}
