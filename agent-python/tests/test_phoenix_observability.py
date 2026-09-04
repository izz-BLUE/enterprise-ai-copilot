"""Phoenix/OpenTelemetry Observability 测试。

全程 patch，不连接真实 Phoenix Collector，也不设置真实 API Key。
"""

import os
from unittest.mock import MagicMock, patch

import pytest

from app.agents.langgraph_agent import run_langgraph_agent
from app.core import observability
from app.core.config import _load_phoenix_settings


@pytest.fixture(autouse=True)
def _reset_observability_state():
    with patch.object(observability, '_provider', None), \
         patch.object(observability, '_tracer', None), \
         patch.object(observability, '_initialization_attempted', False):
        yield


def test_phoenix_settings_default_to_disabled_and_redacted():
    settings = _load_phoenix_settings({})
    assert settings == (
        False,
        'http://localhost:4317',
        'enterprise-ai-copilot',
        1.0,
        False,
    )


@pytest.mark.parametrize('sample_rate', ['-0.1', '1.1', 'invalid'])
def test_phoenix_settings_reject_invalid_sample_rate(sample_rate):
    with pytest.raises(ValueError, match='PHOENIX_SAMPLE_RATE'):
        _load_phoenix_settings({'PHOENIX_SAMPLE_RATE': sample_rate})


def test_initialize_disabled_does_not_load_phoenix():
    with patch.object(observability, 'PHOENIX_TRACING', False), \
         patch.object(
             observability,
             '_register_phoenix_provider',
             side_effect=AssertionError('关闭时不应加载 Phoenix'),
         ) as register_provider:
        assert observability.initialize_observability() is False
    register_provider.assert_not_called()


def test_register_uses_batch_auto_instrumentation_and_sampling():
    provider = MagicMock()
    register = MagicMock(return_value=provider)
    sampler = MagicMock()
    with patch.dict(
        os.environ,
        {
            'OPENINFERENCE_HIDE_INPUTS': 'false',
            'OPENINFERENCE_HIDE_OUTPUTS': 'false',
        },
        clear=False,
    ), \
         patch('phoenix.otel.register', register), \
         patch(
             'opentelemetry.sdk.trace.sampling.TraceIdRatioBased',
             return_value=sampler,
         ), \
         patch.object(observability, 'PHOENIX_CAPTURE_CONTENT', False), \
         patch.object(observability, 'PHOENIX_SAMPLE_RATE', 0.25), \
         patch.object(observability, 'PHOENIX_PROJECT_NAME', 'test-project'), \
         patch.object(
             observability,
             'PHOENIX_COLLECTOR_ENDPOINT',
             'http://phoenix:4317',
         ):
        observability._register_phoenix_provider()
        assert os.environ['OPENINFERENCE_HIDE_INPUTS'] == 'true'
        assert os.environ['OPENINFERENCE_HIDE_OUTPUTS'] == 'true'

    register.assert_called_once_with(
        project_name='test-project',
        endpoint='http://phoenix:4317',
        batch=True,
        auto_instrument=True,
        sampler=sampler,
    )


def test_initialize_failure_is_fail_open_and_not_retried():
    with patch.object(observability, 'PHOENIX_TRACING', True), \
         patch.object(
             observability,
             '_register_phoenix_provider',
             side_effect=RuntimeError('collector config error'),
         ) as register_provider:
        assert observability.initialize_observability() is False
        assert observability.initialize_observability() is False
    register_provider.assert_called_once()


def test_trace_ai_request_records_low_sensitive_attributes():
    span = MagicMock()
    tracer = MagicMock()
    tracer.start_as_current_span.return_value.__enter__.return_value = span
    with patch.object(observability, '_tracer', tracer):
        with observability.trace_ai_request(
            method='POST',
            path='/agent/chat',
            business_trace_id='biz-123',
        ) as active_span:
            assert active_span is span
            observability.record_ai_response(
                active_span,
                status_code=200,
                queue_wait_ms=1.5,
            )

    attributes = {call.args for call in span.set_attribute.call_args_list}
    assert ('business.trace_id', 'biz-123') in attributes
    assert ('http.request.method', 'POST') in attributes
    assert ('url.path', '/agent/chat') in attributes
    assert ('http.response.status_code', 200) in attributes
    assert ('ai.queue_wait_ms', 1.5) in attributes


def test_span_creation_failure_is_fail_open():
    tracer = MagicMock()
    tracer.start_as_current_span.side_effect = RuntimeError('instrumentor failed')
    with patch.object(observability, '_tracer', tracer):
        with observability.trace_ai_request(
            method='POST',
            path='/agent/chat',
            business_trace_id='biz-123',
        ) as active_span:
            assert active_span is None


def test_span_attribute_failure_is_fail_open():
    span = MagicMock()
    span.set_attribute.side_effect = RuntimeError('attribute failed')
    tracer = MagicMock()
    tracer.start_as_current_span.return_value.__enter__.return_value = span
    with patch.object(observability, '_tracer', tracer):
        with observability.trace_ai_request(
            method='POST',
            path='/agent/chat',
            business_trace_id='biz-123',
        ) as active_span:
            assert active_span is span
            observability.record_ai_response(
                active_span,
                status_code=200,
                queue_wait_ms=1.5,
            )


def test_shutdown_failure_does_not_raise():
    provider = MagicMock()
    provider.shutdown.side_effect = RuntimeError('flush failed')
    with patch.object(observability, '_provider', provider):
        observability.shutdown_observability()
    provider.shutdown.assert_called_once()
    assert observability._provider is None


def _fake_graph(final_state: dict):
    graph = MagicMock()
    graph.invoke.return_value = dict(final_state)
    return graph


def test_invoke_metadata_carries_business_trace_id():
    graph = _fake_graph({'answer': 'ok'})
    with patch('app.agents.langgraph_agent.build_agent_graph', return_value=graph):
        run_langgraph_agent('问题', trace_id='biz-123')
    _, kwargs = graph.invoke.call_args
    assert kwargs['config']['metadata'] == {'business_trace_id': 'biz-123'}


def test_invoke_metadata_omitted_when_trace_id_empty():
    graph = _fake_graph({'answer': 'ok'})
    with patch('app.agents.langgraph_agent.build_agent_graph', return_value=graph):
        run_langgraph_agent('问题', trace_id='')
    _, kwargs = graph.invoke.call_args
    assert kwargs['config'] == {}


def test_invoke_metadata_in_planner_loop():
    graph = _fake_graph({'answer': 'ok'})
    with patch('app.agents.langgraph_agent.build_agent_loop_graph', return_value=graph):
        run_langgraph_agent('问题', trace_id='biz-456', use_planner=True)
    _, kwargs = graph.invoke.call_args
    assert kwargs['config']['metadata'] == {'business_trace_id': 'biz-456'}
