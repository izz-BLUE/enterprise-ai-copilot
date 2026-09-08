from urllib.parse import urlparse

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

from app.core.config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    DEEPSEEK_TEMPERATURE,
    LLM_MAX_OUTPUT_TOKENS,
    LLM_MAX_RETRIES,
    LLM_TIMEOUT,
    logger,
)

_client: OpenAI | None = None
_controlled_tool_client: OpenAI | None = None

# Provider 错误分类（应用层语义）。重试次数由配置显式控制。
PROVIDER_ERROR_TIMEOUT = 'provider_timeout'
PROVIDER_ERROR_RATE_LIMITED = 'provider_rate_limited'
PROVIDER_ERROR_UNAVAILABLE = 'provider_unavailable'
EMPTY_CONTENT_MAX_RETRIES = 1


class LLMProviderError(RuntimeError):
    """LLM Provider 错误：携带语义 code，不引入新 retry。

    SDK 仍按其默认 max_retries 处理网络层重试；本异常仅承载
    最终失败时的可观测 code，方便上层日志/分类。
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _classify_provider_error(exc: BaseException) -> LLMProviderError:
    """把 openai SDK 异常映射为 3 类 Provider 错误。

    - APITimeoutError                       → provider_timeout
    - APIStatusError 429 / RateLimitError   → provider_rate_limited
    - APIStatusError 其他 / APIConnectionError → provider_unavailable
    """
    if isinstance(exc, APITimeoutError):
        return LLMProviderError(
            PROVIDER_ERROR_TIMEOUT, f'LLM 调用超时 ({LLM_TIMEOUT}s)',
        )
    if isinstance(exc, APIStatusError):
        status = getattr(exc, 'status_code', None)
        # RateLimitError 是 APIStatusError 子类（status=429）。
        if status == 429 or exc.__class__.__name__ == 'RateLimitError':
            return LLMProviderError(
                PROVIDER_ERROR_RATE_LIMITED,
                f'LLM Provider 限流 (status={status})',
            )
        return LLMProviderError(
            PROVIDER_ERROR_UNAVAILABLE,
            f'LLM Provider 不可用 (status={status})',
        )
    if isinstance(exc, APIConnectionError):
        return LLMProviderError(
            PROVIDER_ERROR_UNAVAILABLE, 'LLM 服务连接失败',
        )
    return LLMProviderError(
        PROVIDER_ERROR_UNAVAILABLE, f'LLM Provider 错误: {exc!r}',
    )


def _build_client(*, max_retries: int = LLM_MAX_RETRIES) -> OpenAI:
    missing = [
        name for name, value in (
            ('DEEPSEEK_API_KEY', DEEPSEEK_API_KEY),
            ('DEEPSEEK_BASE_URL', DEEPSEEK_BASE_URL),
            ('DEEPSEEK_MODEL', DEEPSEEK_MODEL),
        ) if not value
    ]
    if missing:
        raise RuntimeError(f"缺少必需的 Provider 环境变量: {', '.join(missing)}")

    options = {
        'api_key': DEEPSEEK_API_KEY,
        'base_url': DEEPSEEK_BASE_URL,
        'timeout': float(LLM_TIMEOUT),
        'max_retries': max_retries,
    }
    # Phoenix/OpenInference 在应用启动时对 OpenAI SDK 统一自动插桩；这里保持
    # Provider client 的构造与 retry 语义不变，不引入 vendor wrapper。
    return OpenAI(**options)


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = _build_client()
    return _client


def _get_controlled_tool_client() -> OpenAI:
    global _controlled_tool_client
    if _controlled_tool_client is None:
        # 受控业务动作必须只执行一次 HTTP 尝试。
        _controlled_tool_client = _build_client(max_retries=0)
    return _controlled_tool_client


def _response_content(response) -> str:
    choices = getattr(response, 'choices', None) or []
    if not choices:
        return ''
    message = getattr(choices[0], 'message', None)
    return getattr(message, 'content', None) or ''


def _response_request_id(response) -> str:
    request_id = getattr(response, '_request_id', None)
    if request_id:
        return str(request_id)

    raw_response = getattr(response, 'response', None)
    headers = getattr(raw_response, 'headers', None)
    if headers is not None and hasattr(headers, 'get'):
        request_id = headers.get('x-request-id') or headers.get('request-id')
        if request_id:
            return str(request_id)
    return '-'


def _usage_value(value, name: str):
    result = getattr(value, name, None)
    if result is None and isinstance(value, dict):
        result = value.get(name)
    return result if isinstance(result, int) else '-'


def _response_usage(response) -> dict[str, object]:
    usage = getattr(response, 'usage', None)
    if usage is None:
        return {
            'prompt_tokens': '-',
            'completion_tokens': '-',
            'total_tokens': '-',
            'reasoning_tokens': '-',
        }

    details = getattr(usage, 'completion_tokens_details', None)
    if details is None and isinstance(usage, dict):
        details = usage.get('completion_tokens_details')
    return {
        'prompt_tokens': _usage_value(usage, 'prompt_tokens'),
        'completion_tokens': _usage_value(usage, 'completion_tokens'),
        'total_tokens': _usage_value(usage, 'total_tokens'),
        'reasoning_tokens': _usage_value(details, 'reasoning_tokens'),
    }


def _invalid_content_observation(
    response,
    *,
    content_present: bool,
) -> dict[str, object]:
    choices = getattr(response, 'choices', None) or []
    first_choice = choices[0] if choices else None
    finish_reason = getattr(first_choice, 'finish_reason', None) or '-'

    raw_response = getattr(response, 'response', None)
    http_status = getattr(raw_response, 'status_code', None) or 200
    provider = urlparse(DEEPSEEK_BASE_URL or '').netloc or 'unknown'
    usage = _response_usage(response)
    return {
        'provider': provider,
        'model': DEEPSEEK_MODEL,
        'http_status': http_status,
        'provider_request_id': _response_request_id(response),
        'choices_count': len(choices),
        'finish_reason': finish_reason,
        'prompt_tokens': usage['prompt_tokens'],
        'completion_tokens': usage['completion_tokens'],
        'total_tokens': usage['total_tokens'],
        'reasoning_tokens': usage['reasoning_tokens'],
        'content_present': content_present,
    }


def call_llm(
    system_prompt: str,
    user_prompt: str,
    *,
    timeout_seconds: float | None = None,
    response_format: dict[str, str] | None = None,
    thinking: bool | None = None,
) -> str:
    """调用 LLM 并返回首个 choice 的 content 文本。失败时抛 LLMProviderError。

    response_format / thinking 均为可选 Provider 参数；不传时保持默认请求行为。
    thinking=False 显式关闭支持该参数的 Provider 的 thinking 输出。
    对 Provider 成功但没有有效 content 的情况，非 length 结果最多追加一次
    LLM 请求；length 结果直接归类为输出预算耗尽。Provider 异常仍只按 SDK
    自身的 max_retries 行为处理。
    """
    client = _get_client()
    request_options = {}
    if timeout_seconds is not None:
        request_options['timeout'] = max(0.1, min(float(LLM_TIMEOUT), timeout_seconds))
    if response_format is not None:
        request_options['response_format'] = dict(response_format)
    if thinking is not None:
        request_options['extra_body'] = {
            'thinking': {
                'type': 'enabled' if thinking else 'disabled',
            },
        }

    total_attempts = EMPTY_CONTENT_MAX_RETRIES + 1
    for attempt in range(1, total_attempts + 1):
        try:
            response = client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=[
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': user_prompt},
                ],
                temperature=DEEPSEEK_TEMPERATURE,
                max_tokens=LLM_MAX_OUTPUT_TOKENS,
                **request_options,
            )
        except (APITimeoutError, APIStatusError, APIConnectionError) as exc:
            wrapped = _classify_provider_error(exc)
            logger.error('LLM 调用失败: code=%s message=%s', wrapped.code, wrapped)
            raise wrapped from exc

        content = _response_content(response)
        content_present = bool(
            content and (not isinstance(content, str) or content.strip())
        )
        if content_present:
            return content

        observation = _invalid_content_observation(
            response,
            content_present=content_present,
        )
        finish_reason = observation['finish_reason']
        budget_exhausted = finish_reason == 'length'
        reason = 'OUTPUT_BUDGET_EXHAUSTED' if budget_exhausted else 'EMPTY_CONTENT'
        log_total_attempts = attempt if budget_exhausted else total_attempts
        logger.warning(
            'LLM Provider 响应无有效 content: reason=%s provider=%s model=%s '
            'http_status=%s provider_request_id=%s choices=%s '
            'finish_reason=%s prompt_tokens=%s completion_tokens=%s '
            'total_tokens=%s reasoning_tokens=%s content_present=%s '
            'attempt=%s/%s',
            reason,
            observation['provider'],
            observation['model'],
            observation['http_status'],
            observation['provider_request_id'],
            observation['choices_count'],
            observation['finish_reason'],
            observation['prompt_tokens'],
            observation['completion_tokens'],
            observation['total_tokens'],
            observation['reasoning_tokens'],
            observation['content_present'],
            attempt,
            log_total_attempts,
        )

        if budget_exhausted:
            return ''

    # 保持现有失败语义：上层将空文本转换为当前 502 兜底响应。
    return ''
