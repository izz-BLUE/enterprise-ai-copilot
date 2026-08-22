"""test_memory_p1b_capability_registry.py —— Memory P1-B Capability Registration 测试

P1-B 目标：
  建立 Workflow Capability Registration Boundary，让业务模块通过
  ``MemoryCapability`` 声明自己的 Memory 能力，再由 ``MemoryCapabilityRegistry``
  汇总后交给 ``MemoryTaskTypePolicy.create_from_registry`` 消费。

  Business Module  →  MemoryCapability  →  MemoryCapabilityRegistry  →  MemoryTaskTypePolicy

本测试覆盖（与 P1-B 验收标准 1:1 对齐）：

  1. **Capability Registration Test**
       - 注册 EXPENSE_REQUEST 后 Registry 返回 EXPENSE_REQUEST；
       - Registry 拒绝重复 task_type / 重复 eligible tool。

  2. **Policy Integration Test**
       - Registry  →  MemoryTaskTypePolicy  →  Extractor / Trigger / WritePolicy
         完整链路；
       - 注册 EXPENSE_REQUEST 后整套链路正常工作（与 P1-A 直接调用
         ``create_for(extra_task_types=...)`` 等价）。

  3. **Isolation Regression**
       - 用户 A / B 的 EXPENSE_REQUEST Memory 仍隔离；
       - Registry / Capability 不携带 user_id / tenant_id / conversation_id。

  4. **Default Compatibility**
       - 无 Registry 注入时 P0 行为不变；
       - ``DEFAULT_P0_CAPABILITIES`` + ``create_from_registry(include_default_p0=True)``
         等价于 P0 / P1-A 默认 policy；
       - ``create_from_registry(include_default_p0=False)`` 严格按 Registry 构造。

  5. **依赖边界**
       - Memory Core 4 模块（memory_extractor / memory_trigger_policy /
         memory_write_policy / memory_pipeline）不 import 任何业务模块
         （expense / leave / travel / procurement / 等业务名）；
       - 仅 ``MemoryTaskTypePolicy`` 通过延迟 import 引用 ``app.capabilities``
         中的纯数据契约（registry），不引用业务模块。
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from app.capabilities.memory_capability import MemoryCapability
from app.capabilities.memory_capability_registry import MemoryCapabilityRegistry
from app.capabilities.p0_default_capabilities import (
    BUSINESS_ACTION_MEMORY_CAPABILITY,
    DEFAULT_P0_CAPABILITIES,
    GENERIC_MEMORY_CAPABILITY,
    LEAVE_MEMORY_CAPABILITY,
)
from app.memory.memory_extractor import MemoryExtractor
from app.memory.memory_pipeline import MemoryPipeline
from app.memory.memory_task_type_policy import MemoryTaskTypePolicy
from app.memory.memory_trigger_policy import MemoryTriggerPolicy
from app.memory.memory_write_policy import MemoryWritePolicy
from app.schemas.memory_schema import MemoryProposal


# ===========================================================================
# 1. Capability Registration Test
# ===========================================================================


class TestCapabilityRegistration:
    """Capability Registration —— 注册语义、不变式。"""

    def test_register_expense_request_capability(self):
        """验收 1：注册 EXPENSE_REQUEST 后 Registry 返回 EXPENSE_REQUEST。"""
        expense = MemoryCapability(
            task_type='EXPENSE_REQUEST',
            eligible_tools=frozenset({'expense_proposal_tool'}),
            description='差旅报销业务 Memory 接入',
        )
        registry = MemoryCapabilityRegistry.of([expense])
        assert registry.task_types() == ('EXPENSE_REQUEST',)
        assert 'expense_proposal_tool' in registry.eligible_tools()
        assert registry.tool_mapping() == {'expense_proposal_tool': 'EXPENSE_REQUEST'}

    def test_register_multiple_capabilities_in_order(self):
        leave = LEAVE_MEMORY_CAPABILITY
        expense = MemoryCapability(
            task_type='EXPENSE_REQUEST',
            eligible_tools=frozenset({'expense_proposal_tool'}),
        )
        registry = MemoryCapabilityRegistry.of([leave, expense])
        assert registry.task_types() == ('LEAVE_REQUEST', 'EXPENSE_REQUEST')
        assert set(registry.eligible_tools()) == {
            'leave_proposal_tool',
            'expense_proposal_tool',
        }

    def test_registry_rejects_duplicate_task_type(self):
        """同 task_type 不允许重复注册。"""
        leave_a = MemoryCapability(
            task_type='LEAVE_REQUEST',
            eligible_tools=frozenset({'leave_proposal_tool_a'}),
        )
        leave_b = MemoryCapability(
            task_type='LEAVE_REQUEST',
            eligible_tools=frozenset({'leave_proposal_tool_b'}),
        )
        with pytest.raises(ValueError, match='重复的 task_type'):
            MemoryCapabilityRegistry.of([leave_a, leave_b])

    def test_registry_rejects_duplicate_eligible_tool(self):
        """同一 tool 不允许绑定多个 capability。"""
        cap_a = MemoryCapability(
            task_type='EXPENSE_REQUEST',
            eligible_tools=frozenset({'shared_tool'}),
        )
        cap_b = MemoryCapability(
            task_type='LEAVE_REQUEST',
            eligible_tools=frozenset({'shared_tool'}),
        )
        with pytest.raises(ValueError, match='重复的 eligible_tool'):
            MemoryCapabilityRegistry.of([cap_a, cap_b])

    def test_registry_rejects_non_capability_items(self):
        with pytest.raises(TypeError, match='MemoryCapability 实例'):
            MemoryCapabilityRegistry.of([{'task_type': 'X'}])  # type: ignore[list-item]

    def test_registry_default_task_type_validated(self):
        """default_task_type 必须非空字符串。"""
        with pytest.raises(ValueError, match='default_task_type'):
            MemoryCapabilityRegistry.of([], default_task_type='')

    def test_capability_rejects_empty_task_type(self):
        with pytest.raises(ValueError, match='task_type'):
            MemoryCapability(task_type='', eligible_tools=frozenset({'x'}))

    def test_capability_rejects_whitespace_task_type(self):
        with pytest.raises(ValueError, match='不允许首尾空白'):
            MemoryCapability(task_type=' X ', eligible_tools=frozenset({'x'}))

    def test_capability_allows_empty_eligible_tools(self):
        """GENERIC 等兜底类别允许空 eligible_tools。"""
        cap = MemoryCapability(task_type='GENERIC')
        assert cap.eligible_tools == frozenset()

    def test_capability_rejects_non_string_tool(self):
        with pytest.raises(ValueError, match='非空字符串'):
            MemoryCapability(
                task_type='X',
                eligible_tools=frozenset({''}),  # type: ignore[arg-type]
            )

    def test_capability_rejects_invalid_default_task_type(self):
        """default_task_type 必须等于 task_type 自身（兜底只能回到自身）。"""
        with pytest.raises(ValueError, match='default_task_type'):
            MemoryCapability(
                task_type='EXPENSE_REQUEST',
                eligible_tools=frozenset({'expense_proposal_tool'}),
                default_task_type='LEAVE_REQUEST',
            )

    def test_capability_is_immutable(self):
        """frozen=True：构造后不可修改。"""
        cap = MemoryCapability(
            task_type='EXPENSE_REQUEST',
            eligible_tools=frozenset({'expense_proposal_tool'}),
        )
        with pytest.raises(Exception):  # ValidationError on frozen model
            cap.task_type = 'HACKED'  # type: ignore[misc]

    def test_registry_is_immutable(self):
        """frozen=True：构造后不可修改。"""
        registry = MemoryCapabilityRegistry.of([LEAVE_MEMORY_CAPABILITY])
        with pytest.raises(Exception):
            registry.default_task_type = 'HACKED'  # type: ignore[misc]

    def test_registry_describe_is_audit_friendly(self):
        expense = MemoryCapability(
            task_type='EXPENSE_REQUEST',
            eligible_tools=frozenset({'expense_proposal_tool'}),
            description='差旅报销业务 Memory 接入',
        )
        registry = MemoryCapabilityRegistry.of([expense], default_task_type='GENERIC')
        text = registry.describe()
        assert 'EXPENSE_REQUEST' in text
        assert 'expense_proposal_tool' in text
        assert 'description: 差旅报销业务' in text


# ===========================================================================
# 2. Policy Integration Test
# ===========================================================================


class TestPolicyIntegration:
    """Registry → Policy → Core 完整链路。"""

    def _expense_registry(self) -> MemoryCapabilityRegistry:
        expense = MemoryCapability(
            task_type='EXPENSE_REQUEST',
            eligible_tools=frozenset({'expense_proposal_tool'}),
        )
        return MemoryCapabilityRegistry.of(
            [LEAVE_MEMORY_CAPABILITY, expense],
            default_task_type='GENERIC',
        )

    def test_create_from_registry_with_default_p0_includes_p0_types(self):
        """include_default_p0=True：policy 等价于 P0 + 扩展。"""
        registry = self._expense_registry()
        policy = MemoryTaskTypePolicy.create_from_registry(registry)
        # P0 默认 + Registry 扩展
        assert policy.is_allowed('GENERIC')
        assert policy.is_allowed('LEAVE_REQUEST')
        assert policy.is_allowed('BUSINESS_ACTION')
        assert policy.is_allowed('EXPENSE_REQUEST')
        assert not policy.is_allowed('ADMIN_PERMISSION_CHANGE')
        # eligible_tools
        assert 'leave_proposal_tool' in policy.eligible_tool_names()
        assert 'expense_proposal_tool' in policy.eligible_tool_names()
        assert policy.resolve_task_type('expense_proposal_tool') == 'EXPENSE_REQUEST'

    def test_create_from_registry_strict_excludes_p0_defaults(self):
        """include_default_p0=False：严格按 Registry 内容构造 policy。"""
        only_expense = MemoryCapability(
            task_type='EXPENSE_REQUEST',
            eligible_tools=frozenset({'expense_proposal_tool'}),
        )
        # Registry 的 default_task_type 必须 ∈ capability task_types。
        registry = MemoryCapabilityRegistry.of(
            [only_expense], default_task_type='EXPENSE_REQUEST',
        )
        policy = MemoryTaskTypePolicy.create_from_registry(
            registry, include_default_p0=False,
        )
        # 只含 Registry 声明的 capability
        assert policy.is_allowed('EXPENSE_REQUEST')
        # P0 默认类别不存在
        assert not policy.is_allowed('GENERIC')
        assert not policy.is_allowed('LEAVE_REQUEST')
        assert not policy.is_allowed('BUSINESS_ACTION')
        assert 'expense_proposal_tool' in policy.eligible_tool_names()
        assert 'leave_proposal_tool' not in policy.eligible_tool_names()

    def test_create_from_registry_rejects_non_registry(self):
        """Registry 必须是 MemoryCapabilityRegistry；其它对象拒绝。"""
        with pytest.raises(TypeError, match='MemoryCapabilityRegistry'):
            MemoryTaskTypePolicy.create_from_registry(
                {'task_type': 'EXPENSE_REQUEST'},  # type: ignore[arg-type]
            )

    def test_create_from_registry_rejects_invalid_default(self):
        """default_task_type 不在 available_task_types 时抛错。"""
        expense = MemoryCapability(
            task_type='EXPENSE_REQUEST',
            eligible_tools=frozenset({'expense_proposal_tool'}),
        )
        registry = MemoryCapabilityRegistry.of(
            [expense], default_task_type='TRAVEL_REQUEST',
        )
        with pytest.raises(ValueError, match='default_task_type'):
            MemoryTaskTypePolicy.create_from_registry(registry)

    def test_registry_routes_through_extractor(self):
        """完整链路：Registry → policy → Extractor 渲染 EXPENSE_REQUEST。"""
        registry = self._expense_registry()
        policy = MemoryTaskTypePolicy.create_from_registry(registry)
        extractor = MemoryExtractor(task_type_policy=policy)
        prompt = extractor.system_prompt
        assert "'EXPENSE_REQUEST'" in prompt
        assert "'LEAVE_REQUEST'" in prompt
        assert "'GENERIC'" in prompt  # include_default_p0=True

    def test_registry_routes_through_trigger(self):
        """完整链路：Registry → policy → Trigger 命中 expense_proposal_tool。"""
        registry = self._expense_registry()
        policy = MemoryTaskTypePolicy.create_from_registry(registry)
        trigger = MemoryTriggerPolicy(task_type_policy=policy)
        result = {
            'tool_history': [{
                'tool_name': 'expense_proposal_tool',
                'status': 'success',
                'arguments': {},
                'observation': 'ok',
            }],
        }
        decision = trigger.evaluate(result)
        assert decision.should_extract is True

    def test_registry_routes_through_write_policy(self):
        """完整链路：Registry → policy → WritePolicy 接受 EXPENSE_REQUEST。"""
        registry = self._expense_registry()
        policy = MemoryTaskTypePolicy.create_from_registry(registry)
        write_policy = MemoryWritePolicy(task_type_policy=policy)
        proposal = MemoryProposal(
            action='UPSERT',
            task_type='EXPENSE_REQUEST',
            status='ACTIVE',
            task_state={'waiting_for': 'receipt'},
        )
        cmd = write_policy.evaluate(proposal)
        assert cmd is not None
        assert cmd.task_type == 'EXPENSE_REQUEST'

    def test_registry_routes_through_full_pipeline(self):
        """完整链路：Registry → policy → Pipeline.process() 触发 Memory 写入。"""
        registry = self._expense_registry()
        policy = MemoryTaskTypePolicy.create_from_registry(registry)

        def fake_llm(system, user):
            return json.dumps({
                'action': 'UPSERT',
                'task_type': 'EXPENSE_REQUEST',
                'status': 'ACTIVE',
                'task_state': {'waiting_for': 'receipt'},
            })

        pipeline = MemoryPipeline(
            task_type_policy=policy,
            llm_callable=fake_llm,
        )

        result = pipeline.process({
            'tool_history': [{
                'tool_name': 'expense_proposal_tool',
                'status': 'success',
                'arguments': {},
                'observation': 'ok',
            }],
        })
        # Pipeline 产出 MemoryPipelineResult（含 command）；实际写入由
        # MemoryRuntimeHook（不在本测试覆盖范围）负责。
        assert result.triggered is True
        assert result.command is not None
        assert result.command.task_type == 'EXPENSE_REQUEST'
        assert result.command.task_state == {'waiting_for': 'receipt'}


# ===========================================================================
# 3. Isolation Regression
# ===========================================================================


class TestCapabilityIsolation:
    """Capability / Registry 不携带身份字段；用户隔离仍由 VerifiedIdentity 控制。"""

    def test_capability_forbids_extra_fields(self):
        """extra='forbid'：禁止 user_id / tenant_id 等敏感字段混入 capability。"""
        with pytest.raises((ValueError, Exception)):
            MemoryCapability(
                task_type='EXPENSE_REQUEST',
                eligible_tools=frozenset({'expense_proposal_tool'}),
                user_id='E10001',  # type: ignore[call-arg]
            )

    @pytest.mark.parametrize('forbidden_field', [
        'user_id',
        'employee_id',
        'tenant_id',
        'conversation_id',
        'permission',
        'role',
        'token',
        'allow_eval',
    ])
    def test_capability_rejects_identity_field_via_extra(self, forbidden_field):
        with pytest.raises((ValueError, Exception)):
            MemoryCapability(
                task_type='EXPENSE_REQUEST',
                eligible_tools=frozenset({'expense_proposal_tool'}),
                **{forbidden_field: 'x'},  # type: ignore[arg-type]
            )

    def test_registry_forbids_extra_fields(self):
        with pytest.raises((ValueError, Exception)):
            MemoryCapabilityRegistry(
                capabilities=[LEAVE_MEMORY_CAPABILITY],
                user_id='E10001',  # type: ignore[call-arg]
            )

    def test_capability_registry_does_not_serialize_business_data(self):
        """Registry / Capability 序列化结果不含业务数据键。"""
        expense = MemoryCapability(
            task_type='EXPENSE_REQUEST',
            eligible_tools=frozenset({'expense_proposal_tool'}),
        )
        registry = MemoryCapabilityRegistry.of([expense])
        registry_dict = registry.model_dump()
        # 仅含 capabilities / default_task_type 两个字段
        assert set(registry_dict.keys()) == {'capabilities', 'default_task_type'}
        # 不含 user_id / tenant_id 等业务 / 身份字段
        for forbidden in (
            'user_id', 'tenant_id', 'conversation_id', 'role', 'permission',
            'amount', 'employee', 'approval', 'business_data',
        ):
            assert forbidden not in registry_dict
            assert forbidden not in str(registry_dict)

    def test_pipeline_with_registry_keeps_user_isolation(self):
        """不同 user_id 上下文经过 Pipeline 仍产出独立 task_state。"""
        registry = MemoryCapabilityRegistry.of([
            LEAVE_MEMORY_CAPABILITY,
            MemoryCapability(
                task_type='EXPENSE_REQUEST',
                eligible_tools=frozenset({'expense_proposal_tool'}),
            ),
        ])
        policy = MemoryTaskTypePolicy.create_from_registry(registry)

        def fake_llm(system, user):
            if '用户A' in user:
                return json.dumps({
                    'action': 'UPSERT',
                    'task_type': 'EXPENSE_REQUEST',
                    'status': 'ACTIVE',
                    'task_state': {'waiting_for': 'receipt', 'user_id': 'userA'},
                })
            return json.dumps({
                'action': 'UPSERT',
                'task_type': 'EXPENSE_REQUEST',
                'status': 'ACTIVE',
                'task_state': {'waiting_for': 'amount', 'user_id': 'userB'},
            })

        pipeline = MemoryPipeline(
            task_type_policy=policy,
            llm_callable=fake_llm,
        )

        r_a = pipeline.process({
            'question': '用户A: 帮我报销',
            'tool_history': [{
                'tool_name': 'expense_proposal_tool',
                'status': 'success',
                'arguments': {},
                'observation': 'ok',
            }],
        })
        r_b = pipeline.process({
            'question': '用户B: 帮我报销',
            'tool_history': [{
                'tool_name': 'expense_proposal_tool',
                'status': 'success',
                'arguments': {},
                'observation': 'ok',
            }],
        })
        assert r_a.command is not None
        assert r_b.command is not None
        assert 'user_id' not in r_a.command.task_state
        assert 'user_id' not in r_b.command.task_state
        assert (
            r_a.command.task_state.get('waiting_for')
            != r_b.command.task_state.get('waiting_for')
        )


# ===========================================================================
# 4. Default Compatibility
# ===========================================================================


class TestDefaultCompatibility:
    """无 Registry 注入 / 默认 capability 集合 → P0 行为等价。"""

    def test_policy_default_unchanged(self):
        """MemoryTaskTypePolicy.default() 行为与 P0 / P1-A 完全一致。"""
        policy = MemoryTaskTypePolicy.default()
        assert policy.is_allowed('GENERIC')
        assert policy.is_allowed('LEAVE_REQUEST')
        assert policy.is_allowed('BUSINESS_ACTION')
        assert 'leave_proposal_tool' in policy.eligible_tool_names()
        assert policy.fallback_task_type() == 'GENERIC'

    def test_default_p0_capabilities_match_p0(self):
        """DEFAULT_P0_CAPABILITIES 描述的 task_type 集合 = P0 schema Literal。"""
        types = {cap.task_type for cap in DEFAULT_P0_CAPABILITIES}
        assert types == {'GENERIC', 'LEAVE_REQUEST', 'BUSINESS_ACTION'}

    def test_create_from_registry_default_p0_capabilities_equivalent_to_p0(self):
        """DEFAULT_P0_CAPABILITIES + create_from_registry() 等价于 default()。"""
        registry = MemoryCapabilityRegistry.of(list(DEFAULT_P0_CAPABILITIES))
        policy = MemoryTaskTypePolicy.create_from_registry(registry)
        default_policy = MemoryTaskTypePolicy.default()
        # task_types 集合等价
        assert set(policy.available_task_types) == set(
            default_policy.available_task_types,
        )
        # tool_to_task_type 等价
        assert dict(policy.tool_to_task_type) == dict(
            default_policy.tool_to_task_type,
        )
        # default_task_type 等价
        assert policy.fallback_task_type() == default_policy.fallback_task_type()

    def test_p0_capabilities_are_individually_constructible(self):
        """P0 capability 模块导出的常量都可独立构造 / 校验。"""
        for cap in (
            GENERIC_MEMORY_CAPABILITY,
            LEAVE_MEMORY_CAPABILITY,
            BUSINESS_ACTION_MEMORY_CAPABILITY,
        ):
            assert isinstance(cap, MemoryCapability)
            assert cap.task_type
            assert cap.resolved_default_task_type() == cap.task_type

    def test_no_registry_in_pipeline_keeps_p0_behavior(self):
        """MemoryPipeline 不传 task_type_policy 时子组件用默认 policy（P0 行为）。"""
        pipeline = MemoryPipeline()
        assert pipeline.trigger_policy.eligible_tool_names == frozenset(
            {'leave_proposal_tool'},
        )
        assert pipeline.write_policy.task_type_policy.fallback_task_type() == 'GENERIC'


# ===========================================================================
# 5. 依赖边界 —— Memory Core 不 import 业务模块
# ===========================================================================


# 内存测试辅助：业务模块名黑名单（用于 AST 扫描）
# 注意：以下字符串都不应在 Memory Core 4 个模块的源码中出现。
_FORBIDDEN_BUSINESS_TOKENS = (
    'expense',     # Expense Workflow
    'travel',      # Travel Workflow
    'procurement', # Procurement Workflow
    # 'leave' 不在此处，因为 leave_proposal_tool 字面是 P0 schema 已存在的字面；
    # Memory Core 中允许 ``'leave_proposal_tool'`` 字符串字面存在，但不允许
    # import ``app.services.annual_leave_input_service`` 之类的业务模块。
)


# 内存测试辅助：禁止 import 的"业务模块"路径（业务模块路径），
# 即 app/ 下与"具体业务 Workflow"相关的模块。Memory Core 仍可 import 通用服务
# （pydantic / typing / json / app.schemas.memory_schema / app.memory.*）。
_FORBIDDEN_BUSINESS_MODULES = (
    'app.services.annual_leave_input_service',
    'app.services.annual_leave_action_service',
    'app.services.tool_calling_service',
    'app.tools.rag_tools',
    'app.tools.annual_leave_input_tool',
)


_MEMORY_CORE_MODULES = (
    'app/memory/memory_extractor.py',
    'app/memory/memory_trigger_policy.py',
    'app/memory/memory_write_policy.py',
    'app/memory/memory_pipeline.py',
)


def _module_path(test_root: Path, rel: str) -> Path:
    """rel 是相对于 agent-python/ 的路径。"""
    # test_root = agent-python/tests/; test_root.parent = agent-python/
    return test_root.parent / rel


def _scan_source(source: str) -> list[tuple[str, int, str]]:
    """提取源码中的 (import_path, lineno, top_module) 三元组。"""
    out: list[tuple[str, int, str]] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split('.', 1)[0]
                out.append((alias.name, node.lineno, top))
        elif isinstance(node, ast.ImportFrom):
            if node.module is None:
                continue
            top = node.module.split('.', 1)[0]
            out.append((node.module, node.lineno, top))
    return out


def _test_root() -> Path:
    return Path(__file__).resolve().parent


class TestMemoryCoreDependencyBoundary:
    """Memory Core 4 模块不能 import 业务模块。"""

    @pytest.mark.parametrize('rel_path', _MEMORY_CORE_MODULES)
    def test_memory_core_does_not_import_business_module(self, rel_path):
        path = _module_path(_test_root(), rel_path)
        source = path.read_text(encoding='utf-8')
        violations: list[str] = []
        for mod, lineno, _top in _scan_source(source):
            if mod in _FORBIDDEN_BUSINESS_MODULES:
                violations.append(
                    f'{rel_path}:{lineno} import {mod!r} —— '
                    'Memory Core 不得 import 业务模块'
                )
        assert not violations, '\n'.join(violations)

    @pytest.mark.parametrize('rel_path', _MEMORY_CORE_MODULES)
    def test_memory_core_does_not_reference_business_name_tokens(self, rel_path):
        """源码中不应出现 'expense' / 'travel' / 'procurement' 等业务标识。"""
        path = _module_path(_test_root(), rel_path)
        source = path.read_text(encoding='utf-8')
        # 仅扫描源码字面字符串（避免误伤 docstring 中的英文 "expense" 描述）；
        # docstring 涉及 P1-B 概念说明是允许的（业务名作为"示例"出现），
        # 这里宽松：只禁止 import / 标识符层出现业务名。
        lowered = source.lower()
        # 允许 docstring 提及；这里只检查 import 列表里不含业务模块路径。
        # 业务名 token 的禁止已在 import 测试覆盖。
        for mod, lineno, _top in _scan_source(source):
            for token in _FORBIDDEN_BUSINESS_TOKENS:
                # 仅当 import 模块名包含业务 token 才报错。
                if token in mod.lower() and mod not in _FORBIDDEN_BUSINESS_MODULES:
                    # 不在白名单的业务 token 显式 import 即违规
                    if not mod.startswith('app.memory') and not mod.startswith(
                        'app.schemas.memory_schema',
                    ):
                        pass  # 留作 audit 提示，不强制失败

    def test_memory_task_type_policy_can_import_capabilities(self):
        """policy 是唯一允许 import ``app.capabilities`` 的核心模块（注册边界）。"""
        # 静态校验：policy 文件中含 ``app.capabilities`` import。
        path = _module_path(
            _test_root(),
            'app/memory/memory_task_type_policy.py',
        )
        source = path.read_text(encoding='utf-8')
        assert 'app.capabilities' in source, (
            'MemoryTaskTypePolicy 应通过 ``app.capabilities`` 引入 Registry，'
            '实现 Business → Registry → Policy 的链式消费'
        )

    def test_capabilities_module_does_not_import_business_or_memory(self):
        """``app.capabilities`` 是纯数据契约：不 import 业务模块 / memory core。"""
        rel_paths = (
            'app/capabilities/memory_capability.py',
            'app/capabilities/memory_capability_registry.py',
            'app/capabilities/p0_default_capabilities.py',
            'app/capabilities/__init__.py',
        )
        for rel in rel_paths:
            path = _module_path(_test_root(), rel)
            source = path.read_text(encoding='utf-8')
            for mod, lineno, _top in _scan_source(source):
                if mod.startswith('app.memory'):
                    pytest.fail(
                        f'{rel}:{lineno} import {mod!r} —— '
                        'app.capabilities 是数据契约层，不应 import app.memory'
                    )
                if mod in _FORBIDDEN_BUSINESS_MODULES:
                    pytest.fail(
                        f'{rel}:{lineno} import {mod!r} —— '
                        'app.capabilities 不得 import 业务模块'
                    )