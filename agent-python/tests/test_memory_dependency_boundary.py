"""Scoped Conversation Memory Phase 6 依赖边界审计。

约束（与 ``docs/memory-architecture.md`` 第 8 节模块依赖边界对齐）：

  ``app/memory/`` 内所有模块在 import 时不得直接依赖：

    - ``langgraph`` / ``langchain``（除 ``langchain_core`` 的纯类型）
    - ``app.agents.*``（特别是 ``planner_node`` / ``langgraph_agent``）
    - ``app.controllers`` / ``app.routers`` / ``app.api`` / ``app.main``
    - 数据库驱动（``sqlalchemy`` / ``asyncpg`` / ``psycopg``）
    - HTTP 客户端（``httpx`` / ``aiohttp`` / ``requests``）

  本测试以"静态 AST 扫描"方式验证：扫描每个 ``app/memory/*.py`` 源文件，
  检查其 ``import / from-import`` 语句是否触线。

  **注意**：扫描是源文件级别，不运行模块，因此不触发实际副作用。
  这与"memory core modules 可独立 import"的目标一致 —— 即便上层
  LangGraph / Runtime 不可用，memory 包自身依然能 import。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


MEMORY_PACKAGE = Path(__file__).resolve().parents[1] / 'app' / 'memory'

# 禁止 import 的根模块（与 import 路径前缀匹配）。
FORBIDDEN_MODULES = (
    'langgraph',
    'langchain',  # 含 langchain / langchain_openai / langchain_community
    'app.agents',
    'app.agents.planner_node',
    'app.agents.langgraph_agent',
    'app.controllers',
    'app.routers',
    'app.api',
    'app.main',
    'sqlalchemy',
    'asyncpg',
    'psycopg',
    'psycopg2',
    'aiosqlite',
    'httpx',
    'aiohttp',
    'requests',
)

# 允许的 langchain 例外（仅 schema 用的纯类型，不引入副作用）。
LANGCHAIN_ALLOWED_EXCEPTIONS = frozenset({
    'langchain_core',
})


def _iter_memory_modules() -> list[Path]:
    return sorted(p for p in MEMORY_PACKAGE.glob('*.py') if p.name != '__init__.py')


def _is_forbidden(module: str) -> str | None:
    """若 module 命中禁止列表，返回原因；否则返回 None。"""
    for forbidden in FORBIDDEN_MODULES:
        if module == forbidden or module.startswith(forbidden + '.'):
            # langchain 例外：langchain_core 允许
            if forbidden == 'langchain' and module.startswith('langchain_core'):
                continue
            if (
                module.startswith('langchain_core')
                or module in LANGCHAIN_ALLOWED_EXCEPTIONS
            ):
                continue
            return forbidden
    return None


def _extract_imports(source: str) -> list[tuple[str, int]]:
    """返回 (import 路径, 起始行号) 列表。"""
    tree = ast.parse(source)
    imports: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.module is None:
                continue
            imports.append((node.module, node.lineno))
    return imports


def test_memory_package_directory_exists():
    assert MEMORY_PACKAGE.is_dir(), (
        f'未找到 memory 包目录：{MEMORY_PACKAGE}'
    )


@pytest.mark.parametrize('module_path', _iter_memory_modules(),
                         ids=lambda p: p.name)
def test_memory_module_has_no_forbidden_imports(module_path: Path):
    source = module_path.read_text(encoding='utf-8')
    imports = _extract_imports(source)

    violations: list[str] = []
    for module, lineno in imports:
        reason = _is_forbidden(module)
        if reason is not None:
            violations.append(
                f'{module_path.name}:{lineno} import {module!r} '
                f'命中禁止依赖 {reason!r}',
            )

    assert not violations, (
        'app/memory/ 内发现触线 import：\n  ' + '\n  '.join(violations)
    )


def test_memory_modules_import_independently_of_langgraph():
    """memory 包不应在 import 时拉起 LangGraph。

    本测试通过 importlib 直接导入每个 memory 模块，
    然后检查 ``sys.modules`` 中 LangGraph 相关键是否被加载。
    """
    import importlib
    import sys

    forbidden_keys = tuple(
        key for key in sys.modules
        if key == 'langgraph' or key.startswith('langgraph.')
    )
    # 清理可能由其它测试造成的脏 import，然后只 import memory 模块。
    saved = {k: sys.modules.pop(k) for k in forbidden_keys if k in sys.modules}
    try:
        for module_path in _iter_memory_modules():
            module_name = f'app.memory.{module_path.stem}'
            importlib.import_module(module_name)

        leaked = [
            key for key in sys.modules
            if key == 'langgraph' or key.startswith('langgraph.')
        ]
        assert not leaked, (
            'app/memory/* 间接拉起了 langgraph：' + ', '.join(leaked)
        )
    finally:
        # 恢复被清理的模块，避免影响其它测试。
        for key, value in saved.items():
            sys.modules[key] = value


def test_memory_modules_do_not_import_planner_or_agents():
    """显式断言：``app.agents`` 子树（特别是 ``planner_node``）从未被
    ``app/memory/*`` 引用。"""
    for module_path in _iter_memory_modules():
        source = module_path.read_text(encoding='utf-8')
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module == 'app.agents' or node.module.startswith(
                    'app.agents.',
                ):
                    pytest.fail(
                        f'{module_path.name}:{node.lineno} import '
                        f'{node.module!r} —— memory 包不得依赖 app.agents',
                    )


def test_memory_modules_do_not_import_http_clients():
    """任何 HTTP 客户端（``httpx`` / ``aiohttp`` / ``requests``）都被禁止
    直接出现在 ``app/memory/*`` 的 import 列表中。"""
    for module_path in _iter_memory_modules():
        source = module_path.read_text(encoding='utf-8')
        for module, lineno in _extract_imports(source):
            top = module.split('.', 1)[0]
            if top in {'httpx', 'aiohttp', 'requests'}:
                pytest.fail(
                    f'{module_path.name}:{lineno} import {module!r} '
                    '—— memory 包不得直接使用 HTTP 客户端',
                )
