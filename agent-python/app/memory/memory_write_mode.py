"""memory_write_mode.py —— Memory 写入执行模式（Phase 4E）

职责：
  控制 Memory 写入路径是否实际向 Java 返回提案（Disabled / Audit only / Enabled）。
  在生产启用 Memory Proposal 前，应先以 AUDIT_ONLY 模式观察 Pipeline 触发率与
  提案质量，确认无误触发 / 数据合规后再切到 ENABLED。

边界与不变量：
  1. **Mode 决策点**：Policy 决定"是否应该调 Dispatcher"，不修改 command 字段。
  2. **Dispatch 与 Audit 解耦**：
     - DISABLED / AUDIT_ONLY 都不调 Dispatcher；
     - 但 AUDIT_ONLY 仍产生审计事件（write_attempted=False / error_type=None），
       让运营侧知道"如果开启了 Write 实际会发生什么"；
     - DISABLED 是否产生 audit 取决于 Hook（默认不产生，节省空间）。
  3. **构造注入**：mode 通过构造函数注入，不读取环境变量。
     main.py / config layer 负责把 mode 注入到 Runtime Hook。
  4. **不可变**：Policy 不可中途切换 mode（避免审计不一致）；
     如需切换，请重新构造 Hook 整体。

非职责（明确不做）：
  - 不修改 Pipeline / Dispatcher / Java 持久化实现；
  - 不修改 LangGraph / AgentState / Planner / Tool Executor；
  - 不读环境变量 / config 文件；
  - 不修改 MemoryProposal / MemoryWriteCommand 字段。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.memory.memory_write_policy import MemoryWriteCommand

# ---------------------------------------------------------------------------
# Mode 枚举
# ---------------------------------------------------------------------------


MemoryWriteMode = Literal['DISABLED', 'AUDIT_ONLY', 'ENABLED']

# 允许的 mode 集合（用于运行时校验）。
_VALID_MODES: frozenset[str] = frozenset({'DISABLED', 'AUDIT_ONLY', 'ENABLED'})


class MemoryWriteModeError(ValueError):
    """非法 mode 字符串。"""

    def __init__(self, value: object) -> None:
        super().__init__(
            f'MemoryWriteMode 必须是 DISABLED / AUDIT_ONLY / ENABLED，得到 {value!r}'
        )


# ---------------------------------------------------------------------------
# 执行策略
# ---------------------------------------------------------------------------


class MemoryWriteExecutionPolicy(BaseModel):
    """Memory Write 执行模式策略。

    API：
      should_dispatch(command) -> bool
        - True  → Hook 应调 Dispatcher（写入真实 storage）；
        - False → Hook 不调 Dispatcher（仅审计 / 完全关闭）。

      mode() -> MemoryWriteMode
        - 返回当前 mode 字符串。

    决策表：
      command is None                → False（无论 mode）
      mode == 'DISABLED'             → False
      mode == 'AUDIT_ONLY'           → False（仅审计）
      mode == 'ENABLED'              → True

    模型化设计：
      - 用 Pydantic BaseModel 包装（而非普通 dataclass）：
        * 模式校验在构造时完成（mode 不在白名单 → ValidationError）；
        * 不可变语义（构造后 mode 不变；如果未来需要切换，重新构造）。
    """

    model_config = ConfigDict(extra='forbid', frozen=True)

    mode: MemoryWriteMode = Field(default='DISABLED')

    def should_dispatch(self, command: MemoryWriteCommand | None) -> bool:
        """是否调用 Dispatcher。command is None 时永远 False（无法派发）。"""
        if command is None:
            return False
        return self.mode == 'ENABLED'

    def mode_value(self) -> MemoryWriteMode:
        """返回当前 mode 字符串。"""
        return self.mode


# ---------------------------------------------------------------------------
# 独立工厂（便于在 Hook 构造时校验 mode 字面量）
# ---------------------------------------------------------------------------


def make_execution_policy(mode: str) -> MemoryWriteExecutionPolicy:
    """构造 MemoryWriteExecutionPolicy，mode 字面量校验。

    抛出：
      MemoryWriteModeError —— mode 不在白名单。
    """
    if mode not in _VALID_MODES:
        raise MemoryWriteModeError(mode)
    # 走 Pydantic 构造（frozen、extra='forbid' 双层防御）
    return MemoryWriteExecutionPolicy(mode=mode)
