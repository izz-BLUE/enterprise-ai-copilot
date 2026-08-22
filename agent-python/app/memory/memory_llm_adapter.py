"""memory_llm_adapter.py —— MemoryExtractor LLM Runtime Adapter（Phase 3D）

职责：
  将\"任意 LLM client\"包装为 MemoryExtractor.extract() 期望的
  ``Callable[[system_prompt, user_prompt], str]`` 接口。

  - 不绑定具体 SDK（不 import openai / langchain / anthropic）；
  - 不修改 / 不拼接 prompt；
  - 不做 JSON 解析、不做 MemoryProposal 校验、不做 retry / fallback；
    这些都已在 MemoryExtractor / MemoryWritePolicy 内完成。

  错误边界：
    - Client 抛异常 → 原样向上传播（MemoryPipeline 会包装为
      MemoryPipelineError；不应被 adapter 吞掉）；
    - 空响应（None / 空白字符串）→ 抛 MemoryLLMAdapterEmptyResponseError，
      这是 adapter 自己的契约错误，应上抛让 Pipeline / 调用方感知。
"""

from __future__ import annotations

from typing import Any, Callable


# ---- 自定义异常 ----

class MemoryLLMAdapterError(RuntimeError):
    """Memory LLM Adapter 的契约错误基类。"""


class MemoryLLMAdapterEmptyResponseError(MemoryLLMAdapterError):
    """LLM client 返回了空响应（None / 空字符串 / 纯空白）。

    不属于子组件显式声明的\"可预期失败\"，adapter 直接上抛，
    由 MemoryPipeline 包装为 MemoryPipelineError。
    """


# ---- Adapter ----

# LLM client 调用形态：可以是：
#   - 函数对象 callable(system_prompt, user_prompt) -> str
#   - 含 .call(system_prompt, user_prompt) -> str 的对象
LLMClientCallable = Callable[[str, str], str]


class MemoryLLMAdapter:
    """MemoryExtractor  LLM runtime adapter。

    用法：
      adapter = MemoryLLMAdapter(llm_client)
      # 作为 MemoryExtractor.extract() 的 llm_callable 参数注入：
      proposal = extractor.extract(extraction_input, llm_callable=adapter)

    llm_client 必须满足下列形态之一（鸭子类型）：
      - 直接可调用：``client(system_prompt, user_prompt) -> str``
      - 含 .call 方法：``client.call(system_prompt, user_prompt) -> str``
    """

    def __init__(self, llm_client: Any):
        if llm_client is None:
            raise MemoryLLMAdapterError('llm_client 不能为空')
        self._client = llm_client
        self._call: Callable[[str, str], str] = self._resolve_callable(llm_client)

    @staticmethod
    def _resolve_callable(llm_client: Any) -> Callable[[str, str], str]:
        """按 duck typing 解析出统一形态的可调用对象。"""
        if hasattr(llm_client, 'call') and callable(getattr(llm_client, 'call')):
            # 对象形态：llm_client.call(system, user) -> str
            call_method = getattr(llm_client, 'call')

            def via_call(system_prompt: str, user_prompt: str) -> str:
                return call_method(system_prompt, user_prompt)

            via_call.__name__ = f'via_call_on_{type(llm_client).__name__}'
            return via_call
        if callable(llm_client):
            # 函数形态：llm_client(system, user) -> str
            return llm_client  # type: ignore[return-value]
        raise MemoryLLMAdapterError(
            f'llm_client 必须是 callable 或含 .call() 方法，得到 {type(llm_client).__name__}'
        )

    def __call__(self, system_prompt: str, user_prompt: str) -> str:
        """调用底层 LLM client 并返回原始响应字符串。

        - 不修改 / 不拼接 prompt；
        - 不做 JSON 解析 / 字段校验（由 MemoryExtractor.parse_proposal 负责）；
        - 空响应 → MemoryLLMAdapterEmptyResponseError；
        - Client 异常 → 原样向上传播。
        """
        try:
            raw = self._call(system_prompt, user_prompt)
        except MemoryLLMAdapterError:
            # adapter 自己抛的契约错误（来自 _resolve_callable / 客户端内部），
            # 直接上抛
            raise
        except Exception:
            # LLM client 抛出的真实运行时异常（如网络错误、SDK 异常）：
            # 不在 adapter 层包装；让 MemoryPipeline / 调用方决定如何处理
            #（按 Phase 3C-Fix 的 Error Boundary 设计，会被包装为 MemoryPipelineError）。
            raise

        if raw is None:
            raise MemoryLLMAdapterEmptyResponseError(
                'LLM client 返回 None'
            )
        if not isinstance(raw, str):
            raise MemoryLLMAdapterEmptyResponseError(
                f'LLM client 返回了非字符串类型: {type(raw).__name__}'
            )
        if not raw.strip():
            raise MemoryLLMAdapterEmptyResponseError(
                'LLM client 返回了空字符串 / 纯空白响应'
            )
        return raw