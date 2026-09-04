"""Phoenix/OpenTelemetry 可观测性入口。

默认关闭；启用时使用批量异步导出和有界采样。初始化、导出或关闭失败只记录
日志，不改变 RAG、Agent、Tool 或受控业务动作的业务结果。
"""

import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from app.core.config import (
    PHOENIX_CAPTURE_CONTENT,
    PHOENIX_COLLECTOR_ENDPOINT,
    PHOENIX_PROJECT_NAME,
    PHOENIX_SAMPLE_RATE,
    PHOENIX_TRACING,
    logger,
)

_provider: Any | None = None
_tracer: Any | None = None
_initialization_attempted = False

_MASKED_CONTENT_ENV = (
    'OPENINFERENCE_HIDE_INPUTS',
    'OPENINFERENCE_HIDE_OUTPUTS',
    'OPENINFERENCE_HIDE_LLM_PROMPTS',
    'OPENINFERENCE_HIDE_EMBEDDING_VECTORS',
)


def _register_phoenix_provider() -> Any:
    """延迟导入 Phoenix，保证关闭时不加载插桩组件。"""
    from opentelemetry.sdk.trace.sampling import TraceIdRatioBased
    from phoenix.otel import register

    if not PHOENIX_CAPTURE_CONTENT:
        for name in _MASKED_CONTENT_ENV:
            os.environ[name] = 'true'

    return register(
        project_name=PHOENIX_PROJECT_NAME,
        endpoint=PHOENIX_COLLECTOR_ENDPOINT,
        batch=True,
        auto_instrument=True,
        sampler=TraceIdRatioBased(PHOENIX_SAMPLE_RATE),
    )


def initialize_observability() -> bool:
    """初始化 Phoenix 插桩；失败时 fail-open，不阻断应用启动。"""
    global _initialization_attempted, _provider, _tracer

    if _initialization_attempted:
        return _provider is not None
    _initialization_attempted = True
    if not PHOENIX_TRACING:
        return False

    try:
        _provider = _register_phoenix_provider()
        _tracer = _provider.get_tracer('enterprise-ai-copilot.agent-python')
    except Exception:
        _provider = None
        _tracer = None
        logger.exception('Phoenix Observability 初始化失败；业务链路继续运行')
        return False

    logger.info(
        'Phoenix Observability 已启用 project=%s sample_rate=%.3f capture_content=%s',
        PHOENIX_PROJECT_NAME,
        PHOENIX_SAMPLE_RATE,
        PHOENIX_CAPTURE_CONTENT,
    )
    return True


def shutdown_observability() -> None:
    """尽力刷新并关闭 exporter；关闭失败不改变进程退出语义。"""
    global _initialization_attempted, _provider, _tracer

    provider = _provider
    _initialization_attempted = False
    _provider = None
    _tracer = None
    if provider is None:
        return
    try:
        provider.shutdown()
    except Exception:
        logger.exception('Phoenix Observability 关闭失败')


@contextmanager
def trace_ai_request(
    *,
    method: str,
    path: str,
    business_trace_id: str,
) -> Iterator[Any | None]:
    """为 AI HTTP 请求建立根 Span；关闭或初始化失败时为 No-op。"""
    if _tracer is None:
        yield None
        return

    try:
        span_context = _tracer.start_as_current_span(f'{method} {path}')
        span = span_context.__enter__()
    except Exception:
        logger.exception('Phoenix Span 创建失败；业务链路继续运行')
        yield None
        return

    try:
        span.set_attribute('business.trace_id', business_trace_id)
        span.set_attribute('http.request.method', method)
        span.set_attribute('url.path', path)
    except Exception:
        logger.exception('Phoenix Span 属性写入失败；业务链路继续运行')

    try:
        yield span
    except BaseException as exc:
        try:
            span_context.__exit__(type(exc), exc, exc.__traceback__)
        except Exception:
            logger.exception('Phoenix Span 异常关闭失败；保留原业务异常')
        raise
    else:
        try:
            span_context.__exit__(None, None, None)
        except Exception:
            logger.exception('Phoenix Span 关闭失败；业务链路继续运行')


def record_ai_response(
    span: Any | None,
    *,
    status_code: int,
    queue_wait_ms: float | None,
) -> None:
    """记录低敏、低基数的请求结果属性。"""
    if span is None:
        return
    try:
        span.set_attribute('http.response.status_code', status_code)
        if queue_wait_ms is not None:
            span.set_attribute('ai.queue_wait_ms', queue_wait_ms)
    except Exception:
        logger.exception('Phoenix 响应属性写入失败；业务链路继续运行')


def record_routing_shadow(attributes: Mapping[str, object]) -> None:
    """记录 Shadow Routing 的固定低敏字段；观测失败不得影响正式链路。"""
    if _tracer is None:
        return

    allowed_keys = frozenset({
        'routing.shadow.enabled',
        'routing.legacy_action',
        'routing.legacy_tool',
        'routing.shadow_action',
        'routing.shadow_tool',
        'routing.shadow_reason_code',
        'routing.shadow_valid',
        'routing.shadow_guard_allowed',
        'routing.disagreement',
        'routing.shadow_error_code',
    })
    try:
        with _tracer.start_as_current_span('agent.routing.shadow') as span:
            for key, value in attributes.items():
                if key not in allowed_keys or value is None:
                    continue
                if isinstance(value, (str, bool, int, float)):
                    span.set_attribute(key, value)
    except Exception:
        logger.exception('Phoenix Shadow Routing 观测失败；业务链路继续运行')
