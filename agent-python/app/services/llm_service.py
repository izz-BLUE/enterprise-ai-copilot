from openai import APIConnectionError, APITimeoutError, OpenAI

from app.core.config import (
    DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL,
    DEEPSEEK_TEMPERATURE, LLM_TIMEOUT, logger,
)

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        if not DEEPSEEK_API_KEY:
            raise RuntimeError('环境变量 DEEPSEEK_API_KEY 未配置，无法调用 LLM')
        _client = OpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
            timeout=float(LLM_TIMEOUT),
        )
    return _client


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
