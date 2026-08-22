"""Scoped Conversation Memory Phase 5B 离线评估 Case 加载器。

职责：
    - 扫描 ``eval/memory/cases/`` 下的 YAML 文件；
    - 解析为 ``dict`` 后转交 ``MemoryEvaluationCase`` 做严格 schema 校验；
    - 拒绝任何额外字段（``extra='forbid'`` 由 Pydantic 层兜底）；
    - 强制 ``case_id`` 全局唯一；
    - 加载过程是确定性的：按文件名字典序遍历，不依赖文件系统时间戳。

约束：
    - 不调用 LLM、Java、数据库或任何 Memory Runtime；
    - 不修改 ``MemoryEvaluator``；
    - 仅依赖 ``PyYAML``（通过 ``import yaml`` 软加载，缺失时给清晰错误）。
"""

from __future__ import annotations

import importlib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from eval.memory.memory_case_schema import MemoryEvaluationCase


CASES_DIR_NAME = 'cases'
YAML_SUFFIXES = ('.yaml', '.yml')


class MemoryCaseLoadError(ValueError):
    """加载/校验 YAML Case 失败时抛出的可定位错误。"""


def _yaml_module():
    try:
        return importlib.import_module('yaml')
    except ImportError as exc:  # pragma: no cover - 依赖环境检测
        raise MemoryCaseLoadError(
            'PyYAML 未安装：eval/memory/cases loader 需要 PyYAML。'
            '请在 agent-python 下执行 `uv add pyyaml` 后重试。',
        ) from exc


def _default_cases_dir() -> Path:
    return Path(__file__).resolve().parent / CASES_DIR_NAME


def _iter_case_files(cases_dir: Path) -> list[Path]:
    if not cases_dir.is_dir():
        raise MemoryCaseLoadError(
            f'cases 目录不存在：{cases_dir}。请在 eval/memory/cases/ 下放置 YAML case。',
        )
    files = [
        path
        for path in cases_dir.iterdir()
        if path.is_file() and path.suffix.lower() in YAML_SUFFIXES
    ]
    files.sort(key=lambda p: p.name)
    return files


def _load_yaml_text(path: Path) -> Any:
    yaml = _yaml_module()
    try:
        return yaml.safe_load(path.read_text(encoding='utf-8'))
    except yaml.YAMLError as exc:
        raise MemoryCaseLoadError(f'YAML 解析失败：{path} -> {exc}') from exc


def _coerce_to_dict(payload: Any, path: Path) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    raise MemoryCaseLoadError(
        f'Case 顶层必须是 mapping，得到 {type(payload).__name__}：{path}',
    )


def _parse_case(payload: dict[str, Any], path: Path) -> MemoryEvaluationCase:
    try:
        return MemoryEvaluationCase(**payload)
    except ValidationError as exc:
        raise MemoryCaseLoadError(
            f'Case schema 校验失败：{path} -> {exc.errors()}',
        ) from exc


def load_case(path: Path) -> MemoryEvaluationCase:
    """加载单个 YAML case 文件并完成 schema 校验。"""
    if not path.is_file():
        raise MemoryCaseLoadError(f'Case 文件不存在：{path}')
    payload = _coerce_to_dict(_load_yaml_text(path), path)
    return _parse_case(payload, path)


def load_cases(
    cases_dir: Path | None = None,
    *,
    extra_files: Iterable[Path] | None = None,
) -> list[MemoryEvaluationCase]:
    """扫描 ``cases_dir`` 加载所有 YAML case，校验 ``case_id`` 唯一性。

    ``extra_files`` 允许在测试中注入额外路径（仍然走 YAML 解析 +
    schema 校验），不会绕过任何校验。
    """
    base = cases_dir or _default_cases_dir()
    files = list(_iter_case_files(base))
    if extra_files is not None:
        files.extend(Path(p) for p in extra_files)

    # 按文件名排序 + 去重，保证两次调用得到完全相同的列表。
    files = sorted({p.resolve() for p in files}, key=lambda p: p.name)

    origins: dict[str, Path] = {}
    cases: list[MemoryEvaluationCase] = []
    for path in files:
        case = load_case(path)
        existing = origins.get(case.case_id)
        if existing is not None:
            raise MemoryCaseLoadError(
                f'case_id 重复：{case.case_id!r} 同时出现在 '
                f'{existing} 与 {path}',
            )
        origins[case.case_id] = path
        cases.append(case)
    return cases


__all__ = [
    'CASES_DIR_NAME',
    'MemoryCaseLoadError',
    'load_case',
    'load_cases',
]
