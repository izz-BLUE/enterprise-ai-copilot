from openai import APIConnectionError, APITimeoutError, OpenAI

from app.core.config import (
    DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL,
    DEEPSEEK_TEMPERATURE, LLM_TIMEOUT, logger,
)

_client: OpenAI | None = None
_controlled_tool_client: OpenAI | None = None


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
    return OpenAI(**options)


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
        return response.choices[0].message.content
    except APITimeoutError:
        logger.error('LLM 调用超时 (timeout=%ds)', LLM_TIMEOUT)
        raise RuntimeError(f'LLM 调用超时 ({LLM_TIMEOUT}s)')
    except APIConnectionError:
        logger.error('LLM 服务连接失败')
        raise RuntimeError('LLM 服务连接失败')
