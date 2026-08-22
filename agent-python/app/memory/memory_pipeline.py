"""memory_pipeline.py —— Memory Pipeline Orchestrator（Phase 3C / 3C-Fix）

职责：
  串联已有 Memory 组件（Trigger / Extractor / WritePolicy），产出统一的
  MemoryPipelineResult（triggered / proposal / command / trigger_reason / error）。
  - 真实运行时由 main.py 注入现有 llm_service；无注入仅保留为测试 / fail-safe 兼容路径；
  - 不接 Java / 不写数据库（command 由调用方在下游阶段调度 AiTaskMemoryService）；
  - 不修改 LangGraph / AgentState / PlannerDecision / main.py。

Pipeline 行为：
  1. MemoryTriggerPolicy.evaluate(agent_result) → 是否进入 Extract；
  2. 若不触发 → 直接返回 MemoryPipelineResult(triggered=False, ...)；
  3. 若触发 → MemoryExtractionInput.from_agent_result(agent_result) →
     MemoryExtractor.extract(extraction_input, llm_callable) → MemoryProposal；
  4. MemoryWritePolicy.evaluate(proposal) → MemoryWriteCommand（或 None）；
  5. 子组件显式声明的\"可预期失败\"信号（MemoryExtractionParseError /
     NotImplementedError）降级为 noop；其余异常一律重新包装为
     MemoryPipelineError 上抛，由调用方决定如何处理。

Error Boundary 设计（Phase 3C-Fix）：
  Pipeline 内部不再吞掉所有异常；按子组件声明的错误信号分类处理：

    期望降级（fail-safe noop）：
      - MemoryExtractionParseError —— Extractor 输出非法 JSON / 字段校验失败
      - NotImplementedError —— 未注入 llm_callable（仅测试 / fail-safe 兼容路径）

    视为 Pipeline 调度失败（包装为 MemoryPipelineError 抛出）：
      - 任何其他异常（TypeError / ValueError / RuntimeError / AttributeError ...）
        这些通常是子组件或 Pipeline 自身的代码 bug，不应被静默掩盖。

  调用方使用 `MemoryPipelineResult.error` 字段也能拿到失败信息做降级处理；
  抛出与字段并存给两个路径选择权（LangGraph 出口层用 try / 测试桩用 result.error）。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict

from app.memory.memory_extractor import MemoryExtractor, MemoryExtractionParseError
from app.memory.memory_task_type_policy import MemoryTaskTypePolicy
from app.memory.memory_trigger_policy import MemoryTriggerPolicy
from app.memory.memory_write_policy import MemoryWriteCommand, MemoryWritePolicy
from app.schemas.memory_schema import MemoryExtractionInput, MemoryProposal


logger = logging.getLogger(__name__)


class MemoryPipelineError(RuntimeError):
    """Memory Pipeline Orchestrator 的错误信号。

    抛出场景：
      - Pipeline 自身的契约错误（非 dict 输入等）；
      - 子组件抛出的\"非预期\"异常（RuntimeError / ValueError / TypeError 等），
        这些通常是子组件或 Pipeline 自身的代码 bug。

    不抛出的场景（仍按 fail-safe noop 处理）：
      - MemoryExtractionParseError —— Extractor 的\"输出非法\"是合法失败；
      - NotImplementedError —— 未注入 llm_callable 是测试 / fail-safe 兼容路径。
    """


class MemoryPipelineResult(BaseModel):
    """Memory Pipeline 单次执行的最终输出。

    字段语义：
      triggered       —— Trigger Policy 是否判定\"值得调用 Extractor\"。
      proposal        —— Extractor 产出的 MemoryProposal（triggered=False 时恒为 None）。
      command         —— WritePolicy 产出的 MemoryWriteCommand；
                         proposal.action=NONE 或 WritePolicy 拒绝时为 None。
      trigger_reason  —— TriggerPolicy 的 reason 字符串，供 debug / evaluation；
                         业务逻辑不得依赖。
      error           —— MemoryPipelineError（Pipeline 调度失败的内部错误）；
                         成功路径恒为 None。

    注：error 字段类型是自定义异常类，因此 model_config 需要
    arbitrary_types_allowed=True 才能被 Pydantic 接受。
    """

    model_config = ConfigDict(extra='forbid', arbitrary_types_allowed=True)

    triggered: bool
    proposal: MemoryProposal | None = None
    command: MemoryWriteCommand | None = None
    trigger_reason: str = ''
    error: MemoryPipelineError | None = None


class MemoryPipeline:
    """Memory Pipeline Orchestrator。

    用法：
      pipeline = MemoryPipeline()                    # 默认组件 + 无 llm_callable
      pipeline = MemoryPipeline(
          llm_callable=llm_service.call_llm_for_memory,
      )
      try:
          result = pipeline.process(agent_result_dict)
      except MemoryPipelineError as exc:
          # Pipeline 调度失败；按业务策略决定是否降级
          ...

    默认行为（不注入 llm_callable）：
      Extractor.extract() 抛 NotImplementedError → Pipeline 降级为
      triggered=True + proposal=None（fail-safe，不抛错）。
    """

    def __init__(
        self,
        trigger_policy: MemoryTriggerPolicy | None = None,
        extractor: MemoryExtractor | None = None,
        write_policy: MemoryWritePolicy | None = None,
        llm_callable: Callable[[str, str], str] | None = None,
        task_type_policy: MemoryTaskTypePolicy | None = None,
    ):
        # P1-A：所有子组件共享同一个 task_type_policy，避免 trigger / extractor /
        # write_policy 各持一份不一致的白名单。显式注入任意子组件时忽略
        # task_type_policy（兼容既有测试）。
        if task_type_policy is not None:
            resolved_policy = task_type_policy
            self._trigger = trigger_policy or MemoryTriggerPolicy(
                task_type_policy=resolved_policy,
            )
            self._extractor = extractor or MemoryExtractor(
                task_type_policy=resolved_policy,
            )
            self._write_policy = write_policy or MemoryWritePolicy(
                task_type_policy=resolved_policy,
            )
        else:
            self._trigger = trigger_policy or MemoryTriggerPolicy()
            self._extractor = extractor or MemoryExtractor()
            self._write_policy = write_policy or MemoryWritePolicy()
        self._llm_callable = llm_callable

    @property
    def trigger_policy(self) -> MemoryTriggerPolicy:
        return self._trigger

    @property
    def extractor(self) -> MemoryExtractor:
        return self._extractor

    @property
    def write_policy(self) -> MemoryWritePolicy:
        return self._write_policy

    def process(self, agent_result: dict[str, Any]) -> MemoryPipelineResult:
        """串联 Trigger → Extractor → WritePolicy，产出 MemoryPipelineResult。

        抛出：
          MemoryPipelineError —— Pipeline 调度失败（自身契约错误 / 子组件非预期异常）。

        不抛错（fail-safe noop）的情况：
          - 子组件抛 MemoryExtractionParseError（Extractor 输出非法 JSON / 字段）；
          - 子组件抛 NotImplementedError（未注入 llm_callable，兼容路径）。
        """
        if not isinstance(agent_result, dict):
            raise MemoryPipelineError(
                f'MemoryPipeline.process 需要 dict 输入，得到 {type(agent_result).__name__}'
            )

        try:
            return self._process_inner(agent_result)
        except (MemoryExtractionParseError, NotImplementedError):
            # 这两类是子组件显式声明的\"可预期失败\"；不会进入此处
            # （内部 _process_inner 已经分别处理），这里是双重防御。
            raise  # pragma: no cover
        except MemoryPipelineError:
            # 内部已包装的 MemoryPipelineError 直接传出（不要二次包装）
            raise
        except Exception as exc:
            # 任何其他异常（RuntimeError / ValueError / TypeError / AttributeError 等）
            # 一律视为 Pipeline 调度失败（通常是子组件或 Pipeline 自身 bug）；
            # 包装为 MemoryPipelineError 上抛，__cause__ 保留原始异常链。
            logger.warning('MemoryPipeline: 子组件非预期异常: %s', exc)
            raise MemoryPipelineError(
                f'MemoryPipeline 调度失败: {type(exc).__name__}: {exc}'
            ) from exc

    def _process_inner(self, agent_result: dict[str, Any]) -> MemoryPipelineResult:
        # 1. Trigger Policy
        trigger_decision = self._trigger.evaluate(agent_result)
        if not trigger_decision.should_extract:
            return MemoryPipelineResult(
                triggered=False,
                trigger_reason=trigger_decision.reason,
            )

        # 2. MemoryExtractionInput：从 agent_result 白名单映射
        extraction_input = MemoryExtractionInput.from_agent_result(agent_result)

        # 3. MemoryExtractor.extract：可注入 llm_callable；缺省时 NotImplementedError 降级
        try:
            proposal = self._extractor.extract(extraction_input, self._llm_callable)
        except MemoryExtractionParseError as exc:
            logger.warning('MemoryPipeline: parse_proposal 失败: %s', exc)
            return MemoryPipelineResult(
                triggered=True,
                trigger_reason=trigger_decision.reason,
                proposal=None,
            )
        except NotImplementedError:
            logger.info(
                'MemoryPipeline: 未注入 llm_callable，skip extract（triggered=True 但无 proposal）'
            )
            return MemoryPipelineResult(
                triggered=True,
                trigger_reason=trigger_decision.reason,
                proposal=None,
            )

        # 4. MemoryWritePolicy
        command = self._write_policy.evaluate(proposal)

        return MemoryPipelineResult(
            triggered=True,
            trigger_reason=trigger_decision.reason,
            proposal=proposal,
            command=command,
        )
