"""memory_trigger_policy.py —— Memory Trigger Policy（Phase 3B-1 / P1-A 修订）

职责：
  确定一次 Agent 执行结束后，是否值得调用 Memory Extractor。
  - 它不是 LLM / Memory Extractor / MemoryWritePolicy；
  - 不生成 MemoryProposal；
  - 不修改 AgentState / PlannerDecision / LangGraph graph。

  类似于 Safety Guard Lite 之于 Planner：
  Trigger Policy 之于 Memory Extractor。
  它是\"是否进入 Memory Extraction 流程\"的入口守门员，是确定性纯 Python 判定。

设计原则（按 Phase 3A review 调整后的边界）：
  1. Trigger 输入是 LangGraph Agent 终态 dict（含 trusted runtime signal），
     而非 MemoryExtractionInput（后者已剥离 signal，仅承载事实信息）。
  2. trusted signal 用于判定\"是否触发 Extractor\"，**绝不**进入 Extractor 输入。
  3. 判定规则只关心\"Agent 是否产生了跨请求值得保留的工作痕迹\"。

触发规则（满足任一即触发）：
  - action_proposal 非空：用户进入受控业务动作链路（Proposal / Clarification），
    其上下文值得后续会话续接；
  - tool_history 存在成功的 **Memory-eligible Tool** 调用：白名单由注入的
    ``MemoryTaskTypePolicy.eligible_tool_names()`` 提供（不再是 P0 的
    ``MEMORY_TRIGGER_TOOL_NAMES = {LEAVE_PROPOSAL_TOOL_NAME}`` 硬编码）；
    普通查询 Tool（RAG / eval / balance / leave_request）一律不触发；
  - existing_memory（Phase2 Read Path 注入的历史 memory）非空：
    当前会话续接了上一轮 memory，应当尝试更新状态（避免 stale）。

不触发条件（与上述互斥，safety / 短任务 / 失败终态）：
  - 完全空执行：question 空 + 无 tool 调用 + 无 action_proposal + 无 existing_memory；
  - 仅 Safety 拦截（safe=False） → 不进入 Extractor；Safety reason 已记录；
  - Agent 失败终态（route=error 或 stop_reason ∈ provider_error /
    invalid_decision / step_budget_exhausted）→ 不进入 Extractor。
    失败响应没有可信的任务进展，Extractor 可能依据错误文本误判 UPSERT /
    COMPLETE / ABANDON；错误诊断走审计通道，不写任务记忆。

P1-A 演进：
  Trigger 不再写死 tool name 白名单。改为持有 ``MemoryTaskTypePolicy``，
  通过 ``policy.eligible_tool_names()`` 读取"具备 Memory Capability Signal 的
  Tool 集合"。新增业务（例如 EXPENSE_REQUEST）只需在 policy 中注册
  ``expense_proposal_tool → EXPENSE_REQUEST``，Trigger / WritePolicy 自动跟随。

输出：
  MemoryTriggerDecision { should_extract: bool, reason: str }
  - reason 仅供 debug / evaluation，业务逻辑不得依赖。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from app.memory.memory_task_type_policy import MemoryTaskTypePolicy


# 触发原因常量（debug 维度，非业务逻辑）
TRIGGER_REASON_ACTION_PROPOSAL = 'action_proposal_present'
TRIGGER_REASON_TOOL_SUCCESS = 'tool_history_has_success'
TRIGGER_REASON_EXISTING_MEMORY = 'existing_memory_present'

NO_TRIGGER_REASON_NO_SIGNAL = 'no_trigger_signal'
NO_TRIGGER_REASON_SAFETY_BLOCKED = 'safety_blocked'
NO_TRIGGER_REASON_AGENT_FAILURE = 'agent_failure_terminal'

# Agent 失败终态 stop_reason（与 langgraph_agent 响应契约一致）：
# 这些终态下 Agent 没有可信的任务进展，不触发 Memory 写入 / 更新。
_FAILURE_STOP_REASONS = frozenset({
    'provider_error',
    'invalid_decision',
    'step_budget_exhausted',
})


class MemoryTriggerDecision(BaseModel):
    """Memory Trigger Policy 的确定性判定结果。"""

    model_config = ConfigDict(extra='forbid')

    should_extract: bool
    reason: str = ''


class MemoryTriggerPolicy:
    """Memory Trigger 入口守门员。

    用法：
      decision = MemoryTriggerPolicy().evaluate(agent_result_dict)
      if decision.should_extract:
          inp = MemoryExtractionInput.from_agent_result(agent_result_dict)
          proposal = memory_extractor.extract(inp)
          ...

    输入：run_langgraph_agent 返回的 dict（含 trusted runtime signal）。
    输出：MemoryTriggerDecision，pure-function、无副作用。

    P1-A 扩展：
      支持注入 ``task_type_policy``（默认 ``MemoryTaskTypePolicy.default()``）；
      tool_history 中"成功"的判断不再依赖硬编码 ``MEMORY_TRIGGER_TOOL_NAMES``，
      而由 ``policy.eligible_tool_names()`` 动态提供。
    """

    def __init__(self, task_type_policy: MemoryTaskTypePolicy | None = None) -> None:
        self._task_type_policy = task_type_policy or MemoryTaskTypePolicy.default()

    @property
    def task_type_policy(self) -> MemoryTaskTypePolicy:
        return self._task_type_policy

    @property
    def eligible_tool_names(self) -> frozenset[str]:
        """暴露当前 policy 的 Memory-eligible tool 白名单（用于测试 / 诊断）。"""
        return self._task_type_policy.eligible_tool_names()

    def evaluate(self, agent_result) -> MemoryTriggerDecision:
        """评估是否调用 Memory Extractor。

        行为：
          1. Safety 拦截优先：safe=False → 不触发（不与 Safety 语义重叠）；
          2. Agent 失败终态（route=error / stop_reason 失败集合）→ 不触发，
             优先级高于一切正向触发信号（失败时不基于错误文本更新任务记忆）；
          3. 任意触发规则命中 → should_extract=True；
          4. 全部规则未命中 → should_extract=False。

        抛出：
          TypeError —— 入参不是 dict（防御性检查）。
        """
        if not isinstance(agent_result, dict):
            raise TypeError(
                f'MemoryTriggerPolicy.evaluate 需要 dict 输入，得到 {type(agent_result).__name__}'
            )

        # 1. Safety 拦截：直接走 Safety 自身的 category / reason 通道；不重复保存。
        if agent_result.get('safe') is False:
            return MemoryTriggerDecision(
                should_extract=False,
                reason=NO_TRIGGER_REASON_SAFETY_BLOCKED,
            )

        # 2. Agent 失败终态：不进入 Extractor（错误诊断走审计通道）。
        #    即使已有 ACTIVE memory / action_proposal，失败响应也不能作为
        #    UPSERT / COMPLETE / ABANDON 的依据。
        if (
            agent_result.get('route') == 'error'
            or agent_result.get('stop_reason') in _FAILURE_STOP_REASONS
        ):
            return MemoryTriggerDecision(
                should_extract=False,
                reason=NO_TRIGGER_REASON_AGENT_FAILURE,
            )

        # 3. action_proposal 非空：业务动作链路已启动
        action_proposal = agent_result.get('action_proposal')
        if action_proposal:
            return MemoryTriggerDecision(
                should_extract=True,
                reason=TRIGGER_REASON_ACTION_PROPOSAL,
            )

        # 4. 仅允许具备任务连续性价值的 Memory-eligible Tool 成功触发
        #    （白名单由 policy.eligible_tool_names() 动态提供，P1-A 起）
        eligible_tools = self._task_type_policy.eligible_tool_names()
        tool_history = agent_result.get('tool_history') or []
        if any(
            isinstance(item, dict)
            and item.get('status') == 'success'
            and item.get('tool_name') in eligible_tools
            for item in tool_history
        ):
            return MemoryTriggerDecision(
                should_extract=True,
                reason=TRIGGER_REASON_TOOL_SUCCESS,
            )

        # 5. existing_memory 非空：续接上一轮 task memory
        if agent_result.get('memory_context'):
            return MemoryTriggerDecision(
                should_extract=True,
                reason=TRIGGER_REASON_EXISTING_MEMORY,
            )

        # 6. 全部未命中：纯 RAG 完成 / 完全空执行，不值得触发 Extractor
        return MemoryTriggerDecision(
            should_extract=False,
            reason=NO_TRIGGER_REASON_NO_SIGNAL,
        )
