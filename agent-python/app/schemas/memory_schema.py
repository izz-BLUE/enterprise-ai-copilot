"""memory_schema.py —— Memory Write Path 数据契约（Phase 1: 仅定义 MemoryProposal）

设计目标：
  1. Memory Extractor 后续产出的"记忆写意图"在跨层传递（Extractor → WritePolicy → Java）
     时，使用受 Pydantic 严格校验的结构化数据，而不是字符串 JSON。
  2. 信任边界与现有 PlannerDecision 一致：
     - trusted 字段（user_id / employee_id / 权限 / nonce / idempotency_key 等）
       永远由程序层在最终落地时注入，绝不允许出现在 MemoryProposal 内。
     - extra="forbid" 保证任何额外字段直接拒绝（fail-loud 而不是 fail-silent）。
  3. Schema 只负责跨层契约：
     - Extractor / WritePolicy / Java Service 的具体集成由各自边界负责；
     - AgentState 的 memory_context 由 LangGraph 入口按内部请求注入，
       不由本 schema 决定身份或权限。

P1-A 演进：
  - MemoryTaskType（默认白名单参考）：保留 Literal['GENERIC', 'LEAVE_REQUEST',
    'BUSINESS_ACTION']，用于"已知任务类别"的类型提示与文档；
  - MemoryProposal.task_type 改为 ``MemoryTaskTypeStr``（即 ``str`` 类型），
    由 ``MemoryTaskTypePolicy``（policy 模块）独占控制白名单；
    新增业务（例如 EXPENSE_REQUEST）通过 ``MemoryTaskTypePolicy.create_for(
    extra_task_types=('EXPENSE_REQUEST',))`` 注册，**不修改**本 schema 文件。
  - Schema 不再做白名单 fail-loud；policy 在写入链路（MemoryWritePolicy）二次
    校验；任何非法 task_type 在 policy.assert_allowed 处抛 ValueError，Pipeline
    降级为 noop（与现有 MemoryExtractionParseError 一致）。

详细使用约束：
  - task_type：str | None；schema 只保证类型契约；
    合法集合由 ``app.memory.memory_task_type_policy.MemoryTaskTypePolicy`` 控制；
    默认 policy 与 P0 等价。
  - task_state：dict[str, Any] | None；schema 只保证结构契约，不在 schema 层做敏感字段
    过滤（trusted 字段过滤由 MemoryWritePolicy 负责，本 schema 仍以 extra='forbid' 保证
    不出现未声明顶层字段；task_state 内部字段过滤是后续策略层职责）。
  - summary：≤ 500 chars；空字符串视为"无摘要"，允许但不归一化。
  - reason：debug / evaluation 字段；业务逻辑不得依赖。
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---- 枚举 ----

# MemoryProposal.action：提议行为
#  - NONE      ：无记忆建议（例如普通 RAG 完成、无跨请求状态）
#  - UPSERT    ：保存或更新当前任务记忆
#  - COMPLETE  ：把当前 ACTIVE 记忆标记为 COMPLETED
#  - ABANDON   ：把当前 ACTIVE 记忆标记为 ABANDONED（用户放弃 / 任务被取消）
MemoryProposalAction = Literal['NONE', 'UPSERT', 'COMPLETE', 'ABANDON']

# MemoryProposal.task_type 默认白名单参考（保留为类型提示 / 文档）。
# P1-A 起，MemoryProposal.task_type 字段实际类型为 ``str``（见下）；
# 合法集合由 ``MemoryTaskTypePolicy`` 控制；新增任务类别时通过 policy 扩展，
# 无需修改本 Literal。本 Literal 主要用于 IDE 类型提示与"已知集合"的文档化。
MemoryTaskType = Literal['GENERIC', 'LEAVE_REQUEST', 'BUSINESS_ACTION']

# task_type 字段实际类型（P1-A）：由 policy 独占控制白名单，schema 仅做类型校验。
# 取名 ``MemoryTaskTypeStr`` 是为了和保留的 Literal ``MemoryTaskType`` 区分。
MemoryTaskTypeStr = str

# MemoryProposal.status：与 Java 侧 TaskStatus 枚举对应（ACTIVE / COMPLETED / ABANDONED）。
MemoryProposalStatus = Literal['ACTIVE', 'COMPLETED', 'ABANDONED']

# summary 上限与 Java 侧 AiTaskMemoryService.MAX_SUMMARY_CHARS 保持一致。
MEMORY_PROPOSAL_SUMMARY_MAX_CHARS = 500


class MemoryProposal(BaseModel):
    """Memory Extractor 后续产出的"记忆写意图"数据契约。

    P0 / P1-A 约束：
      - 仅定义结构契约，不携带任何 trusted / 系统控制字段；
      - 校验失败必须抛出 ValidationError（fail-loud），不允许静默丢弃或默认值替换；
      - task_type 字段类型放宽为 ``str``（P1-A 起）；合法白名单由
        ``app.memory.memory_task_type_policy.MemoryTaskTypePolicy`` 控制。
    """

    model_config = ConfigDict(extra='forbid')

    action: MemoryProposalAction
    task_type: MemoryTaskTypeStr | None = None
    status: MemoryProposalStatus | None = None
    task_state: dict[str, Any] | None = None
    summary: str = Field(default='', max_length=MEMORY_PROPOSAL_SUMMARY_MAX_CHARS)
    reason: str = ''

    def is_noop(self) -> bool:
        """语义工具方法：是否为\"无记忆建议\"。不参与校验，仅方便调用方分支。"""
        return self.action == 'NONE'


# ---------- Memory Extractor 输入契约 ----------

class MemoryExtractionInput(BaseModel):
    """Agent 执行完成后 → Memory Extractor 的输入契约。

    设计原则：
      Memory Extractor 的职责是判断\"哪些任务状态值得跨请求保存\"——
      不是权限判断器 / Safety 判断器 / Capability 判断器 / Agent 状态审计器。
      因此：

      1. **Trusted Runtime Signal 不能进入 Extractor 的推理输入**：
         route / stop_reason / safe / category / reason / business_date /
         allow_eval / allow_business_actions / missing_fields / trace_id 等
         程序层控制流信号一律不承载于此契约；调用方在\"是否调用 Extractor\"
         的决策点使用它们，而不是让 Extractor 二次判断。
      2. **身份字段永远不在此处承载**：
         employee_id / user_id / conversation_id 等同样不属于 Extractor 输入；
         安全作用域由 Java 侧 (trusted user_id, conversation_id) 复合 key 负责。
      3. **extra='forbid'** 与 MemoryProposal / MemoryWriteCommand 风格一致，
        保证契约封闭；任何额外字段（含 trusted 字段）直接 ValidationError。

    保留的最小事实信息：
      - question / answer：用户输入与最终回答；
      - tool_history：Planner 实际执行的 Tool 调用结果序列；
      - observation：当前图状态的最新观察（与 Tool Executor 同义）；
      - existing_memory：Phase2 Read Path 注入的上一轮 task memory 上下文；
      - action_proposal：受控业务动作 Proposal（如有）。
    """

    model_config = ConfigDict(extra='forbid')

    question: str = ''
    answer: str | None = None
    tool_history: list[dict[str, Any]] = Field(default_factory=list)
    observation: str | None = None
    existing_memory: dict[str, Any] | None = None
    action_proposal: dict[str, Any] | None = None

    @classmethod
    def from_agent_result(cls, result: dict[str, Any]) -> 'MemoryExtractionInput':
        """从 run_langgraph_agent 返回的 dict 构造输入契约。

        行为：
          - 显式列出允许从 result 复制的字段（白名单，避免任何键泄漏）；
          - 缺失字段使用模型默认值；
          - 类型不匹配由 Pydantic ValidationError 兜底。
        """
        allowed_fields = {
            'question', 'answer', 'tool_history', 'observation',
            'memory_context',  # AgentState 字段名 → existing_memory
            'action_proposal',
        }
        payload = {k: v for k, v in result.items() if k in allowed_fields}
        # AgentState 的 memory_context 在契约层重命名为 existing_memory；
        # 这是为了避免 Extractor 误把\"已有 memory\"当成\"待写 memory\"理解。
        if 'memory_context' in payload:
            payload['existing_memory'] = payload.pop('memory_context')
        return cls(**payload)
