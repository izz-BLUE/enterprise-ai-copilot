"""Phase A architecture boundary tests."""

from typing import get_args

import pytest

from app.agents.domain_provider_registry import DOMAIN_PROVIDER_REGISTRY
from app.agents.planner_node import build_planner_system_prompt
from app.agents.tool_catalog import TOOL_CATALOG, ToolCatalog, ToolPromptSpec
from app.agents.workflow_guard.expense_guard import ExpenseGuard
from app.agents.workflow_guard.leave_guard import LeaveGuard
from app.agents.workflow_guard.registry import WorkflowGuardRegistry
from app.schemas.planner_schema import (
    EVAL_TOOL_NAME,
    EXPENSE_PROPOSAL_TOOL_NAME,
    EXPENSE_STATUS_TOOL_NAME,
    INVOICE_VERIFY_TOOL_NAME,
    LEAVE_BALANCE_TOOL_NAME,
    LEAVE_PROPOSAL_TOOL_NAME,
    LEAVE_REQUEST_TOOL_NAME,
    RAG_TOOL_NAME,
    TRAVEL_RECORD_TOOL_NAME,
    ToolName,
)


def test_tool_catalog_matches_planner_and_compatibility_registry_tools():
    planner_tools = tuple(get_args(ToolName))

    assert TOOL_CATALOG.tool_names == planner_tools
    assert DOMAIN_PROVIDER_REGISTRY.tool_names == planner_tools


def test_tool_catalog_rejects_duplicate_tool_names():
    spec = ToolPromptSpec(
        name='duplicate_tool',
        description='',
        argument_contract='',
        reason_code='need_knowledge',
        example={},
    )

    with pytest.raises(ValueError, match='重复 tool name'):
        ToolCatalog((spec, spec))


def test_workflow_guard_registry_rejects_multiple_guard_owners():
    with pytest.raises(ValueError, match='同时归属多个 Guard'):
        WorkflowGuardRegistry((
            ('leave_proposal_tool', LeaveGuard()),
            ('leave_proposal_tool', ExpenseGuard()),
        ))


def test_registry_prompt_specs_are_catalog_metadata():
    for tool_name in TOOL_CATALOG.tool_names:
        assert DOMAIN_PROVIDER_REGISTRY.prompt_spec(tool_name) == TOOL_CATALOG.prompt_spec(tool_name)


def test_planner_prompt_contains_unchanged_catalog_content():
    prompt = build_planner_system_prompt(list(get_args(ToolName)))

    for tool_name in TOOL_CATALOG.tool_names:
        spec = TOOL_CATALOG.prompt_spec(tool_name)
        assert f'- {tool_name}: {spec.description}' in prompt
        assert f'- {tool_name}: {spec.argument_contract}' in prompt
        assert f'tool + {tool_name} → "{spec.reason_code}"' in prompt
        assert spec.usage_rule in prompt
        assert spec.freshness_rule in prompt


def test_selected_tools_have_one_workflow_guard_and_platform_tools_have_none():
    leave_tools = (
        LEAVE_BALANCE_TOOL_NAME,
        LEAVE_REQUEST_TOOL_NAME,
        LEAVE_PROPOSAL_TOOL_NAME,
    )
    expense_tools = (
        TRAVEL_RECORD_TOOL_NAME,
        INVOICE_VERIFY_TOOL_NAME,
        EXPENSE_PROPOSAL_TOOL_NAME,
        EXPENSE_STATUS_TOOL_NAME,
    )

    assert all(
        isinstance(DOMAIN_PROVIDER_REGISTRY.workflow_guard_for_tool(name), LeaveGuard)
        for name in leave_tools
    )
    assert all(
        isinstance(DOMAIN_PROVIDER_REGISTRY.workflow_guard_for_tool(name), ExpenseGuard)
        for name in expense_tools
    )
    assert DOMAIN_PROVIDER_REGISTRY.workflow_guard_for_tool(RAG_TOOL_NAME) is None
    assert DOMAIN_PROVIDER_REGISTRY.workflow_guard_for_tool(EVAL_TOOL_NAME) is None
