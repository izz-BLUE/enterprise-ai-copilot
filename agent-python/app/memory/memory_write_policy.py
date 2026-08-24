"""memory_write_policy.py —— Memory Write Path 安全边界（Phase 2 / P1-A 修订）

职责：
  验证 MemoryProposal 是否允许进入持久化阶段。
  - 它不是 LLM / Planner / Business Action / Permission Engine；
  - 只做确定性安全检查与规范化，输出 MemoryWriteCommand；
  - NONE proposal：直接返回 None（不产生任何写命令）。

边界与不变式：
  1. trusted 字段不允许进入持久化层：
     - 在 task_state 内（嵌套 dict / list 递归）剥离 forbidden 键集；
     - 顶层已由 MemoryProposal.extra='forbid' 拒绝，这里只兜底内层。
  2. 敏感字符串内容脱敏（保守规则，非 NLP）：
     - JWT / Bearer / 显式 password 关键字的字符串值被替换为 '[REDACTED]'；
     - 限制在 summary 与 task_state 的字符串值中执行。
  3. 大小限制与 Java 侧 AiTaskMemoryService 保持一致：
     - task_state JSON 序列化字节 ≤ MAX_TASK_STATE_JSON_BYTES (16 KiB)；
     - summary 长度由 MemoryProposal max_length=500 保证（policy 层不再重复）。
  4. **task_type 白名单校验（P1-A 起）**：
     - 由 ``MemoryTaskTypePolicy.is_allowed`` / ``assert_allowed`` 兜底；
     - 默认 policy 与 P0 等价（GENERIC / LEAVE_REQUEST / BUSINESS_ACTION）；
     - 新增业务（例如 EXPENSE_REQUEST）由调用方扩展 policy，
       WritePolicy 本身不修改。
  5. 输出 MemoryWriteCommand：
     - action 仅为 UPSERT / COMPLETE / ABANDON（不含 NONE）；
     - extra='forbid' 防止后续字段泄漏。

非职责（明确不做）：
  - 不调用 AiTaskMemoryService；
  - 不触碰数据库 / 不修改 LangGraph / 不修改 PlannerDecision；
  - 不实现 Memory Extractor；
  - 不做权限决策（allow_eval / allow_business_actions 由调用方在调用 policy 前已决）；
  - 不控制 task_type 白名单（由注入的 MemoryTaskTypePolicy 控制）。
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from app.memory.memory_task_type_policy import MemoryTaskTypePolicy
from app.schemas.memory_schema import MemoryProposal

# 与 Java 侧 AiTaskMemoryService.MAX_TASK_STATE_JSON_BYTES 对齐 (octet_length <= 16384)。
MAX_TASK_STATE_JSON_BYTES = 16 * 1024

# Command 输出 action（不含 NONE —— NONE 在 policy 层就返回 None，不会走到 command）
CommandAction = Literal['UPSERT', 'COMPLETE', 'ABANDON']


class MemoryTerminalActionNotAllowed(ValueError):
    """禁止 Python 侧发出终态命令（COMPLETE / ABANDON）。

    终态（COMPLETED / ABANDONED）只能由 Java PendingAction 生命周期收口
    （BusinessActionService.closeMemory → AiTaskMemoryService.complete/abandon）；
    Python 不得因为 LLM 输出或重复请求而终结业务动作对应的 Memory。
    这是 MemoryWritePolicy 的第二层程序约束（第一层在 MemoryRuntimeHook）。
    """

# trusted 字段兜底集：必须在 task_state 内（递归）剥离。
# 与 MemoryProposal 顶层 extra='forbid' 互补：
#  - 顶层：schema 校验直接拒绝；
#  - 内层：policy 递归剥离。
_FORBIDDEN_TASK_STATE_KEYS = frozenset({
    'userId',
    'user_id',
    'employeeId',
    'employee_id',
    'conversationId',
    'conversation_id',
    'role',
    'permission',
    'allowEval',
    'allow_eval',
    'allowBusinessActions',
    'allow_business_actions',
    'businessDate',
    'business_date',
    'traceId',
    'trace_id',
    'token',
    'nonce',
    'idempotencyKey',
    'idempotency_key',
    'jwt',
    'password',
})

# 敏感字符串值脱敏规则（非 NLP；仅保守匹配显式关键字）。
# 子串匹配（大小写不敏感）以减小误杀面。**不做长度短路**：task_state 允许到
# 16 KiB，超长字符串同样必须完整扫描，否则 "A"*5000 + " Bearer token" 这类
# 构造会绕过脱敏（历史缺陷，见审计）。单值最坏 16 KiB × 8 个 marker 的扫描
# 成本远低于一次 LLM 调用，不构成性能风险。
_REDACT_MARKERS = ('bearer ', 'jwt', 'password=', 'password:', 'token=', 'token:',
                   'nonce=', 'idempotency-key=')

def _redact_string_value(value: str) -> tuple[str, bool]:
    """对单字符串做敏感字段扫描；命中则整串视为 [REDACTED]。

    返回 (新值, 是否发生脱敏)。保守策略：任意关键字命中即整串替换，
    避免错配位置产生不完整 redaction。
    对任意长度字符串完整扫描（无长度短路）。
    """
    if not value:
        return value, False
    lowered = value.lower()
    if any(marker in lowered for marker in _REDACT_MARKERS):
        return '[REDACTED]', True
    return value, False


def _scrub_task_state(state: dict[str, Any]) -> dict[str, Any]:
    """递归剥离 forbidden 键 + 脱敏字符串值；保持结构稳定。

    生命周期控制字段（status / lifecycle_state 等）由 Java
    AiTaskMemoryService.LIFECYCLE_CONTROL_KEYS 在写入边界剥离，本层不再重复。
    """
    cleaned: dict[str, Any] = {}
    for key, value in state.items():
        if key in _FORBIDDEN_TASK_STATE_KEYS:
            continue
        if isinstance(value, dict):
            cleaned[key] = _scrub_task_state(value)
        elif isinstance(value, list):
            cleaned[key] = _scrub_list(value)
        elif isinstance(value, str):
            new_val, _ = _redact_string_value(value)
            cleaned[key] = new_val
        else:
            cleaned[key] = value
    return cleaned


def _scrub_list(items: list[Any]) -> list[Any]:
    cleaned: list[Any] = []
    for item in items:
        if isinstance(item, dict):
            cleaned.append(_scrub_task_state(item))
        elif isinstance(item, list):
            cleaned.append(_scrub_list(item))
        elif isinstance(item, str):
            new_val, _ = _redact_string_value(item)
            cleaned.append(new_val)
        else:
            cleaned.append(item)
    return cleaned


def _scrub_summary(text: str) -> str:
    """对 summary 字符串做敏感字段脱敏。"""
    if not text:
        return text
    new_val, _ = _redact_string_value(text)
    return new_val


def _check_task_state_size(state: dict[str, Any]) -> None:
    """校验 task_state 序列化字节数；超限时抛 ValueError。"""
    encoded = json.dumps(state, ensure_ascii=False).encode('utf-8')
    if len(encoded) > MAX_TASK_STATE_JSON_BYTES:
        raise ValueError(
            f'task_state 序列化字节数超过 {MAX_TASK_STATE_JSON_BYTES} '
            f'(实际 {len(encoded)})'
        )


class MemoryWriteCommand(BaseModel):
    """policy 输出的"可执行记忆写命令"。

    与 MemoryProposal 的关键区别：
      - action 不含 NONE（NONE 在 policy 层就返回 None）；
      - 字段全部必填（除 summary 外均有显式默认值），调用方无需再做空值处理；
      - extra='forbid' 防止未来扩展意外泄漏。
    """

    model_config = ConfigDict(extra='forbid')

    action: CommandAction
    task_type: str
    status: str
    task_state: dict[str, Any]
    summary: str = ''


class MemoryWritePolicy:
    """Memory Write 确定性安全策略。

    用法：
      cmd = MemoryWritePolicy().evaluate(proposal)
      if cmd is None:
          ...   # NONE: 无写
      else:
          ...   # 调用 Java 侧 AiTaskMemoryService

    P1-A 扩展：
      支持注入 ``task_type_policy``（默认 ``MemoryTaskTypePolicy.default()``）；
      policy 决定合法的 task_type 白名单、默认兜底 task_type，
      WritePolicy 在生成 MemoryWriteCommand 前调用
      ``policy.assert_allowed(task_type)`` 做 fail-loud 兜底。
    """

    # 默认 task_type：当 proposal 未提供时使用；与 Java DEFAULT_TASK_TYPE 对齐。
    # P1-A 起该值仅作为"未注入 policy"时的兜底；正式 policy 由构造注入。
    DEFAULT_TASK_TYPE = 'GENERIC'
    # 默认 task_state：policy 层补齐空 dict，避免下游做 None 分支。
    DEFAULT_TASK_STATE: dict[str, Any] = {}

    def __init__(self, task_type_policy: MemoryTaskTypePolicy | None = None) -> None:
        self._task_type_policy = task_type_policy or MemoryTaskTypePolicy.default()

    @property
    def task_type_policy(self) -> MemoryTaskTypePolicy:
        return self._task_type_policy

    def evaluate(self, proposal: MemoryProposal) -> MemoryWriteCommand | None:
        """评估 MemoryProposal → MemoryWriteCommand（或 None）。

        抛出：
          ValueError —— 输入结构不合法 / task_type 不在 policy 白名单内
                       （policy 层 fail-loud，调用方应降级为 noop）。
          MemoryTerminalActionNotAllowed —— proposal 表达任何终态；终态只能由
                       Java PendingAction 生命周期收口。
        """
        action = proposal.action
        if action == 'NONE':
            return None
        if action not in ('UPSERT', 'COMPLETE', 'ABANDON'):
            # schema 已校验，但 policy 再兜底一次（防御未来 schema 调整）
            raise ValueError(f'未知的 MemoryProposal.action: {action}')

        if (
            action in ('COMPLETE', 'ABANDON')
            or proposal.status in ('COMPLETED', 'ABANDONED')
        ):
            raise MemoryTerminalActionNotAllowed(
                f'Python 响应禁止终态 Memory 命令: '
                f'action={action}, status={proposal.status}；'
                f'终态（COMPLETED / ABANDONED）只能由 Java PendingAction 生命周期收口'
            )

        status = proposal.status
        task_type = proposal.task_type or self._task_type_policy.fallback_task_type()
        # P1-A：policy 二次校验 task_type 是否在白名单内（fail-loud）。
        self._task_type_policy.assert_allowed(task_type)
        raw_state = proposal.task_state if proposal.task_state is not None else self.DEFAULT_TASK_STATE

        # 通过终态守卫后只可能是 UPSERT + ACTIVE。
        if status is None:
            raise ValueError('MemoryProposal.action=UPSERT 必须显式提供 status')
        if proposal.task_state is None:
            raise ValueError('MemoryProposal.action=UPSERT 必须显式提供 task_state')

        # 安全规范化：剥离 forbidden 键 + 字符串脱敏
        scrubbed_state = _scrub_task_state(raw_state)
        _check_task_state_size(scrubbed_state)
        scrubbed_summary = _scrub_summary(proposal.summary)

        return MemoryWriteCommand(
            action=action,
            task_type=task_type,
            status=status,
            task_state=scrubbed_state,
            summary=scrubbed_summary,
        )
