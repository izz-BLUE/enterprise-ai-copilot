"""真实 PostgreSQL 的 LangGraph PostgresSaver 集成验收。

该模块只在显式 POSTGRES 模式运行；默认 Python 全量测试保持不依赖数据库。
"""

import hashlib
import json
import os
from datetime import date
from typing import TypedDict
from unittest.mock import patch

import psycopg
import pytest
from langgraph.graph import END, START, StateGraph

from app.agents.langgraph_agent import run_langgraph_agent
from app.runtime.checkpoint_runtime import CheckpointRuntime

_DSN = os.getenv('LANGGRAPH_CHECKPOINT_DSN', '')
pytestmark = pytest.mark.skipif(
    os.getenv('LANGGRAPH_CHECKPOINT_MODE') != 'POSTGRES' or not _DSN,
    reason='PostgreSQL checkpoint integration requires explicit POSTGRES mode and DSN',
)


class _SnapshotState(TypedDict):
    question: str
    answer: str
    tool_history: list[dict]
    proposal: dict
    proposal_date: date


def _snapshot_node(state: _SnapshotState) -> dict:
    return {'answer': f"saved:{state['question']}"}


def _thread_id(label: str) -> str:
    return 'rt_' + hashlib.sha256(label.encode()).hexdigest()


def _config(thread_id: str) -> dict:
    return {'configurable': {'thread_id': thread_id}}


def _snapshot_graph(runtime: CheckpointRuntime):
    graph = StateGraph(_SnapshotState)
    graph.add_node('snapshot', _snapshot_node)
    graph.add_edge(START, 'snapshot')
    graph.add_edge('snapshot', END)
    return graph.compile(checkpointer=runtime._saver)


@pytest.fixture
def checkpoint_runtime():
    runtime = CheckpointRuntime(
        mode='POSTGRES',
        dsn=_DSN,
        connect_timeout_seconds=3,
        max_connections=3,
    )
    runtime.start()
    try:
        yield runtime
    finally:
        runtime.shutdown()


def test_p1_p3_p9_real_write_strict_serializer_and_trusted_state_boundary(checkpoint_runtime):
    graph = _snapshot_graph(checkpoint_runtime)
    thread_id = _thread_id('p1-p3-p9')
    graph.invoke(
        {
            'question': 'checkpoint write',
            'answer': '',
            'tool_history': [{'tool_name': 'rag_answer_tool', 'status': 'success'}],
            'proposal': {'kind': 'draft', 'fields': ['reason']},
            'proposal_date': date(2026, 8, 27),
        },
        config=_config(thread_id),
        durability='sync',
    )

    snapshot = graph.get_state(_config(thread_id))
    assert snapshot.values['answer'] == 'saved:checkpoint write'
    assert snapshot.values['proposal_date'] == date(2026, 8, 27)

    # P3: 使用实际 AgentState + Runtime Context 执行，可信字段不应写入 checkpoint values。
    agent_thread_id = _thread_id('p3-agent') + ':deterministic-v1'
    with patch('app.agents.langgraph_agent.rag_answer_tool') as rag_tool:
        rag_tool.invoke.return_value = json.dumps({
            'answer': 'ok',
            'success': True,
            'sources': [],
        })
        run_langgraph_agent(
            '查询制度',
            allow_eval=True,
            allow_business_actions=True,
            business_date=date(2026, 8, 27),
            trace_id='trusted-trace',
            employee_id='E10001',
            graph=checkpoint_runtime.get_graph(use_planner=False),
            runtime_thread_id=agent_thread_id,
        )
    agent_snapshot = checkpoint_runtime.get_graph(use_planner=False).get_state(
        _config(agent_thread_id)
    )
    assert not {
        'employee_id', 'allow_eval', 'allow_business_actions',
        'business_date', 'trace_id', 'deadline_monotonic',
    }.intersection(agent_snapshot.values)


def test_p2_p11_p12_checkpoint_survives_runtime_restart_and_setup_is_idempotent():
    thread_id = _thread_id('p2-restart')
    runtime_a = CheckpointRuntime(
        mode='POSTGRES', dsn=_DSN, connect_timeout_seconds=3, max_connections=3,
    )
    runtime_a.start()
    try:
        graph_a = _snapshot_graph(runtime_a)
        graph_a.invoke(
            {
                'question': 'persist across runtime restart',
                'answer': '',
                'tool_history': [],
                'proposal': {'kind': 'restart'},
                'proposal_date': date(2026, 8, 27),
            },
            config=_config(thread_id),
            durability='sync',
        )
    finally:
        runtime_a.shutdown()

    runtime_b = CheckpointRuntime(
        mode='POSTGRES', dsn=_DSN, connect_timeout_seconds=3, max_connections=3,
    )
    runtime_b.start()
    try:
        graph_b = _snapshot_graph(runtime_b)
        assert graph_b.get_state(_config(thread_id)).values['answer'] == (
            'saved:persist across runtime restart'
        )
        with psycopg.connect(_DSN, autocommit=True) as connection:
            rows = connection.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"
            ).fetchall()
        table_names = {row[0] for row in rows}
        assert {'checkpoints', 'checkpoint_writes', 'checkpoint_blobs', 'checkpoint_migrations'} <= table_names
    finally:
        runtime_b.shutdown()


def test_p4_p7_threads_and_graph_variants_are_isolated(checkpoint_runtime):
    graph = _snapshot_graph(checkpoint_runtime)
    thread_a = _thread_id('thread-a')
    thread_b = _thread_id('thread-b')
    planner_thread = _thread_id('same-base') + ':planner-v1'
    deterministic_thread = _thread_id('same-base') + ':deterministic-v1'
    for thread_id, question in (
        (thread_a, 'thread A'),
        (thread_b, 'thread B'),
        (planner_thread, 'planner history'),
        (deterministic_thread, 'deterministic history'),
    ):
        graph.invoke(
            {
                'question': question,
                'answer': '',
                'tool_history': [],
                'proposal': {},
                'proposal_date': date(2026, 8, 27),
            },
            config=_config(thread_id),
            durability='sync',
        )

    assert graph.get_state(_config(thread_a)).values['question'] == 'thread A'
    assert graph.get_state(_config(thread_b)).values['question'] == 'thread B'
    assert graph.get_state(_config(planner_thread)).values['question'] == 'planner history'
    assert graph.get_state(_config(deterministic_thread)).values['question'] == 'deterministic history'


def test_p8_new_request_resets_current_request_tool_history(checkpoint_runtime):
    graph = checkpoint_runtime.get_graph(use_planner=True)
    thread_id = _thread_id('p8-tool-history') + ':planner-v1'
    tool_decision = json.dumps({
        'action': 'tool',
        'tool_name': 'rag_answer_tool',
        'arguments': {'question': 'first request'},
        'reason_code': 'need_knowledge',
    })
    finish_decision = json.dumps({
        'action': 'finish',
        'answer': 'done',
        'reason_code': 'completed',
    })
    with patch('app.agents.planner_node.call_llm', side_effect=[tool_decision, finish_decision]), \
            patch('app.agents.tool_executor_node.rag_answer_tool') as rag_tool:
        rag_tool.invoke.return_value = json.dumps({'answer': 'tool answer', 'success': True, 'sources': []})
        first = run_langgraph_agent(
            'first request', use_planner=True, graph=graph, runtime_thread_id=thread_id,
        )
    assert first['tool_history']

    with patch('app.agents.planner_node.call_llm', return_value=finish_decision):
        second = run_langgraph_agent(
            'second request', use_planner=True, graph=graph, runtime_thread_id=thread_id,
        )
    assert second['tool_history'] == []
    assert graph.get_state(_config(thread_id)).values['tool_history'] == []


def test_p10_postgres_unavailable_fails_closed_without_disabled_fallback():
    runtime = CheckpointRuntime(
        mode='POSTGRES',
        dsn='postgresql://127.0.0.1:1/checkpoint_unavailable',
        connect_timeout_seconds=1,
        max_connections=1,
    )
    with pytest.raises(RuntimeError, match='PostgreSQL checkpoint 初始化失败'):
        runtime.start()
    assert runtime.enabled is True
