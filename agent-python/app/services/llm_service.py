from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

from app.core.config import (
    DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL,
    DEEPSEEK_TEMPERATURE, LANGSMITH_TRACING, LLM_TIMEOUT, logger,
)

_client: OpenAI | None = None
_controlled_tool_client: OpenAI | None = None

# Provider 错误分类（应用层语义）。SDK 默认 retry 行为保持不变。
PROVIDER_ERROR_TIMEOUT = 'provider_timeout'
PROVIDER_ERROR_RATE_LIMITED = 'provider_rate_limited'
PROVIDER_ERROR_UNAVAILABLE = 'provider_unavailable'


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


def _build_client(*, max_retries: int | None = None) -> OpenAI:
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
    }
    if max_retries is not None:
        options['max_retries'] = max_retries
    client = OpenAI(**options)
    if LANGSMITH_TRACING:
        # 统一插桩：Planner / 旧 SDK RAG / 受控业务动作的裸 SDK 调用都经此 client
        from langsmith.wrappers import wrap_openai
        return wrap_openai(client)
    return client


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        # Preserve the SDK's existing retry behavior for standard RAG calls.
        _client = _build_client()
    return _client


def _get_controlled_tool_client() -> OpenAI:
    global _controlled_tool_client
    if _controlled_tool_client is None:
        # Controlled business actions must make exactly one HTTP attempt.
        _controlled_tool_client = _build_client(max_retries=0)
    return _controlled_tool_client


def call_llm(system_prompt: str, user_prompt: str) -> str:
    """调用 LLM 并返回首个 choice 的 content 文本。失败时抛 LLMProviderError。

    应用层不引入额外网络 retry；SDK 自身的 max_retries 行为保持不变。
    """
    client = _get_client()
    try:
        response = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
            temperature=DEEPSEEK_TEMPERATURE,
        )
    except (APITimeoutError, APIStatusError, APIConnectionError) as exc:
        wrapped = _classify_provider_error(exc)
        logger.error('LLM 调用失败: code=%s message=%s', wrapped.code, wrapped)
        raise wrapped from exc

    # 空响应防御——不引入应用层网络 retry，仅做 None / 空白归一化。
    if not response.choices:
        return ''
    return response.choices[0].message.content or ''
