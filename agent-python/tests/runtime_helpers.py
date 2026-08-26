"""Helpers for invoking Agent nodes with explicit LangGraph runtime context."""

from langgraph.runtime import Runtime

from app.agents.runtime_context import AgentRuntimeContext

TRUSTED_CONTEXT_FIELDS = frozenset({
    'employee_id',
    'allow_eval',
    'allow_business_actions',
    'business_date',
    'trace_id',
    'deadline_monotonic',
})

_DEFAULT_CONTEXT: AgentRuntimeContext = {
    'employee_id': '',
    'allow_eval': False,
    'allow_business_actions': False,
    'business_date': None,
    'trace_id': 'test-trace',
    'deadline_monotonic': float('inf'),
}


def runtime_for_state(state: dict, **overrides) -> Runtime[AgentRuntimeContext]:
    """Build explicit context from legacy test fixture inputs.

    Existing fixture builders still use the old names to describe a request;
    the node receives only the stripped state plus this explicit Runtime.
    """
    context = dict(_DEFAULT_CONTEXT)
    context.update({key: state[key] for key in TRUSTED_CONTEXT_FIELDS if key in state})
    context.update(overrides)
    return Runtime(context=context)


def checkpoint_safe_state(state: dict) -> dict:
    """Remove request-trusted fields before passing a fixture as AgentState."""
    return {
        key: value for key, value in state.items()
        if key not in TRUSTED_CONTEXT_FIELDS
    }
