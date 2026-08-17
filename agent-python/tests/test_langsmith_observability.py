"""LangSmith Observability P0 测试。

验证两点：
1. llm_service._build_client 按 LANGSMITH_TRACING 条件包装 OpenAI client——
   关闭时返回原生 client（行为与不接入完全一致），开启时经
   langsmith.wrappers.wrap_openai 插桩（Planner / 旧 SDK RAG / 受控业务动作共用）；
2. run_langgraph_agent 把业务 trace_id 作为 business_trace_id 传入
   LangGraph invoke 的 config metadata，空 trace_id 时不注入。

全程 patch，不访问真实 LangSmith 网络，不设置真实 LANGSMITH_API_KEY。
"""

import contextlib

from unittest.mock import MagicMock, patch

from app.agents.langgraph_agent import run_langgraph_agent
from app.services import llm_service


def _patch_llm_env():
    """_build_client 要求三个 Provider 变量非空，测试环境可能未配置。"""
    return (
        patch.object(llm_service, 'DEEPSEEK_API_KEY', 'test-key'),
        patch.object(llm_service, 'DEEPSEEK_BASE_URL', 'https://provider.test/v1'),
        patch.object(llm_service, 'DEEPSEEK_MODEL', 'test-model'),
    )


def _stack_llm_env(stack: contextlib.ExitStack):
    for cm in _patch_llm_env():
        stack.enter_context(cm)


def test_build_client_not_wrapped_when_tracing_disabled():
    """LANGSMITH_TRACING=false（默认）：返回原生 OpenAI client，不触碰 wrap_openai。"""
    fake_client = MagicMock()
    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(llm_service, 'LANGSMITH_TRACING', False))
        _stack_llm_env(stack)
        openai_cls = stack.enter_context(
            patch.object(llm_service, 'OpenAI', return_value=fake_client))
        wrap = stack.enter_context(patch(
            'langsmith.wrappers.wrap_openai',
            side_effect=AssertionError('关闭时不应调用 wrap_openai'),
        ))
        client = llm_service._build_client()
    openai_cls.assert_called_once()
    assert client is fake_client
    wrap.assert_not_called()


def test_build_client_wrapped_when_tracing_enabled():
    """LANGSMITH_TRACING=true：client 经 wrap_openai 插桩后返回。"""
    fake_client = MagicMock()
    instrumented = MagicMock()
    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(llm_service, 'LANGSMITH_TRACING', True))
        _stack_llm_env(stack)
        stack.enter_context(patch.object(llm_service, 'OpenAI', return_value=fake_client))
        wrap = stack.enter_context(patch(
            'langsmith.wrappers.wrap_openai', return_value=instrumented))
        client = llm_service._build_client()
    wrap.assert_called_once_with(fake_client)
    assert client is instrumented


def test_controlled_tool_client_wrapped_when_tracing_enabled():
    """受控业务动作 client 与普通 client 走同一 _build_client，同样被插桩。"""
    fake_client = MagicMock()
    instrumented = MagicMock()
    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(llm_service, 'LANGSMITH_TRACING', True))
        _stack_llm_env(stack)
        stack.enter_context(patch.object(llm_service, '_controlled_tool_client', None))
        stack.enter_context(patch.object(llm_service, 'OpenAI', return_value=fake_client))
        wrap = stack.enter_context(patch(
            'langsmith.wrappers.wrap_openai', return_value=instrumented))
        client = llm_service._get_controlled_tool_client()
    wrap.assert_called_once_with(fake_client)
    assert client is instrumented


def _fake_graph(final_state: dict):
    graph = MagicMock()
    graph.invoke.return_value = dict(final_state)
    return graph


def test_invoke_metadata_carries_business_trace_id():
    """默认图路径：业务 trace_id 以 business_trace_id 进入 invoke config metadata。"""
    graph = _fake_graph({'answer': 'ok', 'step_count': 1, 'tool_call_count': 0,
                         'stop_reason': 'task_complete'})
    with patch('app.agents.langgraph_agent.build_agent_graph', return_value=graph):
        run_langgraph_agent('问题', trace_id='biz-123')
    args, kwargs = graph.invoke.call_args
    assert kwargs['config']['metadata'] == {'business_trace_id': 'biz-123'}
    assert 'business_trace_id' in kwargs['config']['metadata']


def test_invoke_metadata_omitted_when_trace_id_empty():
    """trace_id 为空：不注入 metadata，config 为空 dict。"""
    graph = _fake_graph({'answer': 'ok'})
    with patch('app.agents.langgraph_agent.build_agent_graph', return_value=graph):
        run_langgraph_agent('问题', trace_id='')
    args, kwargs = graph.invoke.call_args
    assert kwargs['config'] == {}


def test_invoke_metadata_in_planner_loop():
    """Agent Loop（use_planner=True）路径同样传递 business_trace_id。"""
    graph = _fake_graph({'answer': 'ok'})
    with patch('app.agents.langgraph_agent.build_agent_loop_graph', return_value=graph):
        run_langgraph_agent('问题', trace_id='biz-456', use_planner=True)
    args, kwargs = graph.invoke.call_args
    assert kwargs['config']['metadata'] == {'business_trace_id': 'biz-456'}
