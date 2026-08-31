"""第一版两个写业务任务契约的确定性分解。"""

from __future__ import annotations

import re

from app.schemas.task_decomposition_schema import TaskDecompositionResult, TaskSpec
from app.services.annual_leave_input_service import is_annual_leave_action_intent
from app.services.expense_input_service import is_expense_claim_intent

# 有意支持 ``请个假`` 以服务队列的 clarification 流程，同时保持现有年假单任务
# router 不变。
_GENERIC_LEAVE_ACTION_PATTERN = re.compile(
    r"(?:申请|我要|我想|帮我|请|休)[^，。；,\n]{0,12}(?:请假|请个假|休假)"
)
_QUERY_WORDS = (
    "流程",
    "制度",
    "规定",
    "政策",
    "标准",
    "怎么",
    "如何",
    "需要什么",
    "材料",
    "手续",
    "审批",
)
_UNSUPPORTED_WRITE_MARKERS = (
    "加班申请",
    "调休申请",
    "出差申请",
    "采购申请",
    "借款申请",
    "付款申请",
    "转账",
)

# 只在程序识别的协调边界处分割。这些固定字面候选使扫描复杂度保持为用户输入
# 长度的线性关系。匹配到的连接词不属于任一任务，因此每个返回的 task_text
# 都是原始用户消息中的精确连续片段。
_TASK_BOUNDARY_PUNCTUATION = frozenset("，,。；;\n")
_PUNCTUATION_CONNECTORS = (
    "然后",
    "另外",
    "再",
    "接着",
    "并且",
    "并",
    "同时",
    "之后",
    "以后",
    "最后",
    "再帮我",
    "再把",
)
_CONNECTORS = (
    "然后",
    "另外",
    "接着",
    "同时",
    "并且",
    "并",
    "以及",
    "再",
    "之后",
    "以后",
)
_ACTION_PREFIXES = (
    "帮我",
    "请",
    "申请",
    "把",
    "将",
    "给我",
    "我要",
    "我想",
    "报销",
    "报账",
    "休",
)


def _is_generic_leave_action(text: str) -> bool:
    normalized = text.strip()
    if not normalized or any(word in normalized for word in _QUERY_WORDS):
        return False
    return _GENERIC_LEAVE_ACTION_PATTERN.search(normalized) is not None


def _task_types(text: str) -> list[str]:
    types: list[str] = []
    if is_annual_leave_action_intent(text) or _is_generic_leave_action(text):
        types.append("LEAVE_REQUEST")
    if is_expense_claim_intent(text):
        types.append("EXPENSE_CLAIM")
    return types


def _task_type(text: str) -> str | None:
    types = _task_types(text)
    return types[0] if len(types) == 1 else None


def _first_literal_at(text: str, index: int, candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        if text.startswith(candidate, index):
            return candidate
    return None


def _skip_whitespace(text: str, index: int) -> int:
    while index < len(text) and text[index].isspace():
        index += 1
    return index


def _iter_task_boundaries(question: str):
    """在线性扫描 ``question`` 的过程中产出已识别的边界区间。"""
    index = 0
    while index < len(question):
        if question[index] in _TASK_BOUNDARY_PUNCTUATION:
            connector_start = _skip_whitespace(question, index + 1)
            connector = _first_literal_at(
                question, connector_start, _PUNCTUATION_CONNECTORS)
            if connector is not None:
                boundary_end = connector_start + len(connector)
                yield index, boundary_end
                index = boundary_end
                continue
            # 不要从每个换行处重新扫描长空白段。当空白段之后没有标点连接词时，
            # 后续空白字符不可能开始另一个匹配。
            if connector_start > index + 1:
                index = connector_start
                continue

        connector = _first_literal_at(question, index, _CONNECTORS)
        if connector is not None:
            action_start = _skip_whitespace(question, index + len(connector))
            if _first_literal_at(question, action_start, _ACTION_PREFIXES) is not None:
                boundary_end = index + len(connector)
                yield index, boundary_end
                index = boundary_end
                continue
        index += 1


def _split_segments(question: str) -> list[str]:
    """按所有已识别的协调边界分割，供全局校验使用。"""
    segments: list[str] = []
    start = 0
    for boundary_start, boundary_end in _iter_task_boundaries(question):
        segment = question[start:boundary_start].strip()
        if segment:
            segments.append(segment)
        start = boundary_end
    tail = question[start:].strip()
    if tail:
        segments.append(tail)
    return segments


def decompose_write_tasks(question: str) -> TaskDecompositionResult:
    """不询问 LLM，识别恰好两个受支持的写业务任务。

    单个受支持意图、只读请求或普通混合请求继续走现有路径。如果某个协调边界
    明确包含超过两个受支持的写业务任务，或无法安全地分类成两个任务局部片段，
    结果为 ``unsupported``，调用方不得执行业务 Tool。
    """

    normalized = question.strip()
    segments = _split_segments(normalized)
    segment_types = [_task_type(segment) for segment in segments]
    known_count = sum(len(_task_types(segment)) for segment in segments)
    if any(len(_task_types(segment)) > 1 for segment in segments):
        return TaskDecompositionResult(
            kind="unsupported",
            reason="当前消息中的多个写业务无法安全拆分，请拆分后重试。",
        )
    if known_count > 2:
        return TaskDecompositionResult(
            kind="unsupported",
            reason="只支持最多两个有序写业务任务。",
        )
    has_unknown_write = any(
        task_type is None
        and any(marker in segment for marker in _UNSUPPORTED_WRITE_MARKERS)
        for segment, task_type in zip(segments, segment_types)
    )
    if known_count >= 2 and has_unknown_write:
        return TaskDecompositionResult(
            kind="unsupported",
            reason="当前消息包含不支持的第三种写业务，请拆分后重试。",
        )

    if len(segments) == 2 and all(segment_types):
        if segment_types[0] == segment_types[1]:
            return TaskDecompositionResult(
                kind="unsupported",
                reason="第一版仅支持年假和报销两个不同的写业务任务。",
            )
        return TaskDecompositionResult(
            kind="multi",
            tasks=[
                TaskSpec(task_type=segment_types[0], task_text=segments[0], sequence=1),
                TaskSpec(task_type=segment_types[1], task_text=segments[1], sequence=2),
            ],
        )

    if known_count == 2 and len(segments) == 1:
        return TaskDecompositionResult(
            kind="unsupported",
            reason="当前消息中的两个写业务缺少安全分隔边界，请拆分后重试。",
        )
    return TaskDecompositionResult(kind="single")
