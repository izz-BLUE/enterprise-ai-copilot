"""Deterministic decomposition for the first two write-task contract."""

from __future__ import annotations

import re

from app.schemas.task_decomposition_schema import TaskDecompositionResult, TaskSpec
from app.services.annual_leave_input_service import is_annual_leave_action_intent
from app.services.expense_input_service import is_expense_claim_intent

# ``请个假`` is intentionally supported for the queue's clarification flow,
# while the existing annual-leave single-task router remains unchanged.
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

# Split only at a program-recognized coordination boundary.  The matched
# connector belongs to neither task, so each returned task_text remains an
# exact contiguous span of the original user message.
_TASK_BOUNDARY = re.compile(
    r"(?:[，,。；;\n]\s*(?:然后|另外|再|接着|并且|并|同时|之后|以后|最后|再帮我|再把)"
    r"|(?:然后|另外|接着|同时|并且|并|以及|再|之后|以后)"
    r"(?=\s*(?:帮我|请|申请|把|将|给我|我要|我想|报销|报账|休))"
    r")"
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


def _split_segments(question: str) -> list[str]:
    """Split all recognized coordination boundaries for global validation."""
    segments: list[str] = []
    start = 0
    for boundary in _TASK_BOUNDARY.finditer(question):
        segment = question[start:boundary.start()].strip()
        if segment:
            segments.append(segment)
        start = boundary.end()
    tail = question[start:].strip()
    if tail:
        segments.append(tail)
    return segments


def decompose_write_tasks(question: str) -> TaskDecompositionResult:
    """Detect exactly two supported write tasks without asking the LLM.

    A single supported intent, a read-only request, or an ordinary mixed
    request remains on the existing path.  If a coordination boundary clearly
    contains more than the two supported write tasks, or cannot be safely
    classified into two task-local spans, the result is ``unsupported`` and
    the caller must not execute a business Tool.
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
