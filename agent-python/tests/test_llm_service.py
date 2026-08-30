"""Model Reliability P0：LLM Provider 错误分类 + 空响应归一化测试。

覆盖：
1. call_llm 在 Provider 超时 / 限流 / 不可用 / 其他异常时的 code 分类；
2. call_llm 对 choices 为空 / message.content 为 None / 空白字符串归一化为 ''；
3. _get_controlled_tool_client 不受 LLMProviderError 影响（保持 max_retries=0）。
"""

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from openai import APIConnectionError, APIStatusError, APITimeoutError

from app.services import llm_service
from app.services.llm_service import (
    PROVIDER_ERROR_RATE_LIMITED,
    PROVIDER_ERROR_TIMEOUT,
    PROVIDER_ERROR_UNAVAILABLE,
    LLMProviderError,
    call_llm,
)


def _timeout_exc():
    return APITimeoutError(request=MagicMock())


def _connection_exc():
    return APIConnectionError(request=MagicMock())


def _status_exc(status_code: int):
    response = MagicMock()
    response.status_code = status_code
    return APIStatusError(
        'status error', response=response, body=None,
    )


class _FakeChoice:
    def __init__(self, content):
        self.message = MagicMock(content=content)


class _FakeResponse:
    def __init__(self, choices):
        self.choices = choices


class TestClassifyProviderError:
    def test_timeout_maps_to_provider_timeout(self):
        err = llm_service._classify_provider_error(_timeout_exc())
        assert isinstance(err, LLMProviderError)
        assert err.code == PROVIDER_ERROR_TIMEOUT

    def test_429_maps_to_provider_rate_limited(self):
        err = llm_service._classify_provider_error(_status_exc(429))
        assert err.code == PROVIDER_ERROR_RATE_LIMITED

    def test_5xx_maps_to_provider_unavailable(self):
        err = llm_service._classify_provider_error(_status_exc(503))
        assert err.code == PROVIDER_ERROR_UNAVAILABLE

    def test_4xx_non_429_maps_to_provider_unavailable(self):
        err = llm_service._classify_provider_error(_status_exc(400))
        assert err.code == PROVIDER_ERROR_UNAVAILABLE

    def test_connection_error_maps_to_provider_unavailable(self):
        err = llm_service._classify_provider_error(_connection_exc())
        assert err.code == PROVIDER_ERROR_UNAVAILABLE


class TestCallLlmErrorPropagation:
    def _patch_client(self, side_effect):
        client = MagicMock()
        client.chat.completions.create.side_effect = side_effect
        return patch.object(llm_service, '_get_client', return_value=client)

    def test_call_llm_raises_provider_timeout(self):
        with self._patch_client(_timeout_exc()):
            try:
                call_llm('sys', 'usr')
            except LLMProviderError as exc:
                assert exc.code == PROVIDER_ERROR_TIMEOUT
            else:
                raise AssertionError('LLMProviderError 未抛出')

    def test_call_llm_raises_provider_rate_limited(self):
        with self._patch_client(_status_exc(429)):
            try:
                call_llm('sys', 'usr')
            except LLMProviderError as exc:
                assert exc.code == PROVIDER_ERROR_RATE_LIMITED
            else:
                raise AssertionError('LLMProviderError 未抛出')

    def test_call_llm_raises_provider_unavailable_on_5xx(self):
        with self._patch_client(_status_exc(500)):
            try:
                call_llm('sys', 'usr')
            except LLMProviderError as exc:
                assert exc.code == PROVIDER_ERROR_UNAVAILABLE
            else:
                raise AssertionError('LLMProviderError 未抛出')

    def test_call_llm_raises_provider_unavailable_on_connection(self):
        with self._patch_client(_connection_exc()):
            try:
                call_llm('sys', 'usr')
            except LLMProviderError as exc:
                assert exc.code == PROVIDER_ERROR_UNAVAILABLE
            else:
                raise AssertionError('LLMProviderError 未抛出')


class TestCallLlmEmptyResponse:
    def _patch_client(self, response):
        client = MagicMock()
        client.chat.completions.create.return_value = response
        return patch.object(llm_service, '_get_client', return_value=client)

    def test_choices_empty_returns_empty_string(self):
        with self._patch_client(_FakeResponse(choices=[])):
            assert call_llm('sys', 'usr') == ''

    def test_content_none_returns_empty_string(self):
        with self._patch_client(_FakeResponse(choices=[_FakeChoice(None)])):
            assert call_llm('sys', 'usr') == ''

    def test_content_empty_returns_empty_string(self):
        with self._patch_client(_FakeResponse(choices=[_FakeChoice('')])):
            assert call_llm('sys', 'usr') == ''

    def test_content_normal_returns_as_is(self):
        with self._patch_client(_FakeResponse(choices=[_FakeChoice('hello')])):
            assert call_llm('sys', 'usr') == 'hello'

    def test_default_call_does_not_force_provider_specific_options(self):
        client = MagicMock()
        client.chat.completions.create.return_value = _FakeResponse(
            choices=[_FakeChoice('hello')],
        )
        with patch.object(llm_service, '_get_client', return_value=client):
            assert call_llm('sys', 'usr') == 'hello'

        kwargs = client.chat.completions.create.call_args.kwargs
        assert 'response_format' not in kwargs
        assert 'extra_body' not in kwargs

    def test_optional_json_output_and_thinking_are_forwarded(self):
        client = MagicMock()
        client.chat.completions.create.return_value = _FakeResponse(
            choices=[_FakeChoice('hello')],
        )
        with patch.object(llm_service, '_get_client', return_value=client):
            call_llm(
                'sys',
                'usr',
                response_format={'type': 'json_object'},
                thinking=False,
            )

        kwargs = client.chat.completions.create.call_args.kwargs
        assert kwargs['response_format'] == {'type': 'json_object'}
        assert kwargs['extra_body'] == {'thinking': {'type': 'disabled'}}


class TestControlledClientUnchanged:
    """确认 Model Reliability P0 未触碰 controlled client（max_retries=0）。"""

    def test_controlled_tool_client_uses_zero_retries(self):
        captured_kwargs: dict = {}

        class _StubOpenAI:
            def __init__(self, **kwargs):
                captured_kwargs.update(kwargs)

        # 注入 _build_client 要求的三项 Provider 变量，避免依赖运行环境。
        with patch.object(llm_service, 'OpenAI', _StubOpenAI), \
             patch.object(llm_service, '_controlled_tool_client', None), \
             patch.object(llm_service, 'DEEPSEEK_API_KEY', 'test-key'), \
             patch.object(llm_service, 'DEEPSEEK_BASE_URL', 'https://provider.test/v1'), \
             patch.object(llm_service, 'DEEPSEEK_MODEL', 'test-model'):
            client = llm_service._get_controlled_tool_client()

        assert captured_kwargs['max_retries'] == 0
        assert client.__class__.__name__ == '_StubOpenAI'

    def test_provider_error_is_not_retried_by_application(self):
        client = MagicMock()
        client.chat.completions.create.side_effect = _timeout_exc()
        with patch.object(llm_service, '_get_client', return_value=client):
            try:
                call_llm('sys', 'usr')
            except LLMProviderError as exc:
                assert exc.code == PROVIDER_ERROR_TIMEOUT
            else:
                raise AssertionError('LLMProviderError 未抛出')

        assert client.chat.completions.create.call_count == 1
