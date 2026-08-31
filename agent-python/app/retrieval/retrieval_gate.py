from dataclasses import dataclass
from threading import Lock
from time import perf_counter

from app.core.config import logger


@dataclass(frozen=True)
class CandidateSignals:
    chunk_id: str
    vector_score: float | None = None
    bm25_score: float | None = None
    vector_rank: int | None = None
    bm25_rank: int | None = None


@dataclass(frozen=True)
class GateDecision:
    answerable: bool
    reason_code: str
    mode_reason_code: str
    matched_chunk_id: str | None = None
    matched_vector_score: float | None = None
    matched_bm25_score: float | None = None


_metrics_lock = Lock()
_metrics = {
    'gate_evaluation_error': 0,
    'gate_disabled': 0,
}


def evaluate_gate(
    candidates: list[CandidateSignals],
) -> GateDecision:
    """固定关闭检索 Gate，保留信号和日志接口供质量观测使用。"""
    del candidates
    decision = GateDecision(True, 'gate_disabled', 'gate_disabled')
    _increment_metric('gate_disabled')
    return decision


def evaluate_gate_timed(candidates: list[CandidateSignals]) -> tuple[GateDecision, float]:
    started = perf_counter()
    decision = evaluate_gate(candidates)
    return decision, (perf_counter() - started) * 1000


def evaluate_gate_timed_fail_open(
    candidates: list[CandidateSignals], *, trace_id: str,
) -> tuple[GateDecision, float]:
    """对固定关闭的 Gate evaluator 异常保持 fail-open。"""
    started = perf_counter()
    try:
        decision = evaluate_gate(candidates)
    except Exception as exc:
        _increment_metric('gate_evaluation_error')
        logger.error(
            '[%s] rag_gate evaluation failed mode=off exception_type=%s fail_open=true',
            trace_id, type(exc).__name__,
        )
        decision = GateDecision(
            answerable=True,
            reason_code='gate_evaluation_error',
            mode_reason_code='gate_disabled',
        )
    return decision, (perf_counter() - started) * 1000


def log_gate_event(
    *,
    trace_id: str,
    decision: GateDecision,
    candidate_count: int,
    retrieval_latency_ms: float,
    gate_latency_ms: float,
    llm_called: bool,
) -> None:
    logger.info(
        '[%s] rag_gate mode=%s answerable=%s reason_code=%s mode_reason=%s '
        'candidate_count=%d matched_chunk_id=%s vector_score=%s bm25_score=%s '
        'retrieval_latency_ms=%.2f gate_latency_ms=%.3f llm_called=%s',
        trace_id, 'off', decision.answerable, decision.reason_code,
        decision.mode_reason_code, candidate_count,
        decision.matched_chunk_id or '-',
        _format_score(decision.matched_vector_score),
        _format_score(decision.matched_bm25_score),
        retrieval_latency_ms, gate_latency_ms, llm_called,
    )


def get_gate_metrics() -> dict[str, int]:
    with _metrics_lock:
        return dict(_metrics)


def reset_gate_metrics() -> None:
    with _metrics_lock:
        for key in _metrics:
            _metrics[key] = 0


def _increment_metric(name: str) -> None:
    with _metrics_lock:
        _metrics[name] += 1


def _format_score(score: float | None) -> str:
    return '-' if score is None else f'{score:.6f}'
