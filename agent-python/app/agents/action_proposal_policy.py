"""供 Java HITL 使用的可确认 Proposal 共享确定性策略。"""

from typing import Any

from app.agents.domain_provider_registry import DOMAIN_PROVIDER_REGISTRY

# 只接受显式注册 Provider 声明的 proposal Tool；未注册值 fail-closed。
PROPOSAL_TOOL_NAMES = frozenset(
    name
    for provider in DOMAIN_PROVIDER_REGISTRY.providers
    for name in provider.proposal_tool_names
)


def is_confirmable_action_proposal(state: dict[str, Any]) -> bool:
    """返回最终状态是否包含真正可确认的 Proposal。

    该判断由 HITL 路由和最终化共享。Clarification 不满足条件，因为它有意不包含
    action proposal。
    """
    if state.get('stop_reason') != 'task_complete':
        return False
    if state.get('action_proposal') is None:
        return False

    last_success: str | None = None
    for entry in state.get('tool_history', []) or []:
        if isinstance(entry, dict) and entry.get('status') == 'success':
            last_success = entry.get('tool_name')
    return last_success in PROPOSAL_TOOL_NAMES
