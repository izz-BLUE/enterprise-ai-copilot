from dataclasses import dataclass
from threading import Lock
from time import perf_counter

from app.core.config import (
    RAG_BM25_WEAK_THRESHOLD,
    RAG_GATE_MODE,
    RAG_VECTOR_STRONG_THRESHOLD,
    RAG_VECTOR_WEAK_THRESHOLD,
    logger,
)


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
    'evaluated': 0,
    'shadow_pass': 0,
    'shadow_block': 0,
    'enforce_pass': 0,
    'enforce_block': 0,
    'gate_evaluation_error': 0,
    'gate_disabled': 0,
    'llm_called_after_shadow_block': 0,
}


def evaluate_gate(
    candidates: list[CandidateSignals],
    *,
    mode: str | None = None,
    vector_strong: float = RAG_VECTOR_STRONG_THRESHOLD,
    vector_weak: float = RAG_VECTOR_WEAK_THRESHOLD,
    bm25_weak: float = RAG_BM25_WEAK_THRESHOLD,
) -> GateDecision:
    """基于同一候选的原始 Vector/BM25 信号计算确定性门控决策。"""
    if mode is None:
        mode = RAG_GATE_MODE
    if mode == 'off':
        decision = GateDecision(True, 'gate_disabled', 'gate_disabled')
        _increment_metric('gate_disabled')
        return decision
    if mode not in {'shadow', 'enforce'}:
        raise ValueError(f'运行时不支持 RAG gate mode: {mode!r}')

    pass_reason = f'{mode}_pass'
    block_reason = f'{mode}_block'

    _increment_metric('evaluated')
    if not candidates:
        decision = GateDecision(False, 'empty_candidates', block_reason)
        _increment_metric(block_reason)
        return decision

    for candidate in candidates:
        vector_score = candidate.vector_score
        if vector_score is not None and vector_score >= vector_strong:
            decision = GateDecision(
                True, 'vector_strong', pass_reason, candidate.chunk_id,
                vector_score, candidate.bm25_score,
            )
            _increment_metric(pass_reason)
            return decision

    for candidate in candidates:
        vector_score = candidate.vector_score
        bm25_score = candidate.bm25_score
        if (
            vector_score is not None
            and bm25_score is not None
            and vector_score >= vector_weak
            and bm25_score >= bm25_weak
        ):
            decision = GateDecision(
                True, 'vector_bm25_weak_combined', pass_reason,
                candidate.chunk_id, vector_score, bm25_score,
            )
            _increment_metric(pass_reason)
            return decision

    decision = GateDecision(False, 'below_threshold', block_reason)
    _increment_metric(block_reason)
    return decision


def evaluate_gate_timed(candidates: list[CandidateSignals]) -> tuple[GateDecision, float]:
    started = perf_counter()
    decision = evaluate_gate(candidates)
    return decision, (perf_counter() - started) * 1000


def evaluate_gate_timed_fail_open(
    candidates: list[CandidateSignals], *, trace_id: str,
) -> tuple[GateDecision, float]:
    """仅对 Gate evaluator 自身异常执行 Shadow fail-open。"""
    started = perf_counter()
    try:
        decision = evaluate_gate(candidates)
    except Exception as exc:
        _increment_metric('gate_evaluation_error')
        logger.error(
            '[%s] rag_gate evaluation failed mode=%s exception_type=%s fail_open=true',
            trace_id, RAG_GATE_MODE, type(exc).__name__,
        )
        enforce = RAG_GATE_MODE == 'enforce'
        decision = GateDecision(
            answerable=not enforce,
            reason_code='gate_evaluation_error',
            mode_reason_code='enforce_error_block' if enforce else 'shadow_fail_open',
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
    if decision.mode_reason_code == 'shadow_block' and llm_called:
        _increment_metric('llm_called_after_shadow_block')
    logger.info(
        '[%s] rag_gate mode=%s answerable=%s reason_code=%s mode_reason=%s '
        'candidate_count=%d matched_chunk_id=%s vector_score=%s bm25_score=%s '
        'retrieval_latency_ms=%.2f gate_latency_ms=%.3f llm_called=%s',
        trace_id, RAG_GATE_MODE, decision.answerable, decision.reason_code,
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
