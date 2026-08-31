from dataclasses import dataclass
from time import perf_counter

from app.core.config import DEEPSEEK_MODEL, logger
from app.prompts.system_prompt import SYSTEM_PROMPT, build_rag_prompt
from app.retrieval.hybrid_retriever import retrieve_with_signals
from app.retrieval.query_rewriter import rewrite_query
from app.retrieval.retrieval_gate import evaluate_gate_timed_fail_open, log_gate_event
from app.services.llm_service import call_llm

NO_KNOWLEDGE_ANSWER = '当前知识库暂无相关信息'
UNAVAILABLE_ANSWER = '当前 AI 服务暂时不可用，请稍后重试。'


@dataclass(frozen=True)
class RagAnswerResult:
    answer: str
    model: str
    success: bool
    sources: list[str]


def _source_labels(chunks: list[dict]) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        domain = str(chunk.get('domain') or 'unknown')
        source_file = str(chunk.get('source_file') or chunk.get('id') or 'unknown')
        chunk_index = chunk.get('chunk_index')
        suffix = f'#chunk-{chunk_index}' if chunk_index is not None else ''
        label = f'{domain}/{source_file}{suffix}'
        if label not in seen:
            seen.add(label)
            labels.append(label)
    return labels


def answer_rag(
    question: str,
    *,
    trace_id: str,
    top_k: int = 3,
    retrieval_mode: str = 'hybrid',
    rewrite_mode: str | None = None,
    retrieval_query: str | None = None,
) -> RagAnswerResult:
    """统一的生产 RAG 生成入口，供普通问答和 Agent Tool 共同复用。

    生产路径固定不重写 query；rewrite_mode 仅保留给离线测试/评估调用方。
    """
    effective_retrieval_query = retrieval_query
    if effective_retrieval_query is None:
        rewrite_result = rewrite_query(
            question,
            mode='none' if rewrite_mode is None else rewrite_mode,
        )
        effective_retrieval_query = rewrite_result['rewritten_query']
        if rewrite_result['rewrite_applied']:
            logger.info(
                '[%s] Query rewrite applied reason=%s',
                trace_id,
                rewrite_result['rewrite_reason'],
            )

    gate_context = None
    llm_called = False
    try:
        retrieval_started = perf_counter()
        chunks, candidate_signals = retrieve_with_signals(
            effective_retrieval_query,
            top_k=top_k,
            mode=retrieval_mode,
        )
        retrieval_latency_ms = (perf_counter() - retrieval_started) * 1000
        gate_decision, gate_latency_ms = evaluate_gate_timed_fail_open(
            candidate_signals,
            trace_id=trace_id,
        )
        gate_context = (
            gate_decision,
            len(candidate_signals),
            retrieval_latency_ms,
            gate_latency_ms,
        )
        logger.info(
            '[%s] Retrieval completed mode=%s chunk_count=%d',
            trace_id,
            retrieval_mode,
            len(chunks),
        )

        if not chunks:
            return RagAnswerResult(
                answer=NO_KNOWLEDGE_ANSWER,
                model=DEEPSEEK_MODEL,
                success=True,
                sources=[],
            )

        logger.info('[%s] 开始调用 LLM', trace_id)
        llm_called = True
        answer = call_llm(SYSTEM_PROMPT, build_rag_prompt(question, chunks))
        if not (answer or '').strip():
            logger.warning('[%s] LLM 返回空响应，走失败兜底', trace_id)
            return RagAnswerResult(
                answer=UNAVAILABLE_ANSWER,
                model=DEEPSEEK_MODEL,
                success=False,
                sources=[],
            )

        return RagAnswerResult(
            answer=answer,
            model=DEEPSEEK_MODEL,
            success=True,
            sources=_source_labels(chunks),
        )
    except Exception:
        logger.exception('[%s] RAG 回答链路失败', trace_id)
        return RagAnswerResult(
            answer=UNAVAILABLE_ANSWER,
            model=DEEPSEEK_MODEL,
            success=False,
            sources=[],
        )
    finally:
        if gate_context is not None:
            decision, candidate_count, retrieval_ms, gate_ms = gate_context
            log_gate_event(
                trace_id=trace_id,
                decision=decision,
                candidate_count=candidate_count,
                retrieval_latency_ms=retrieval_ms,
                gate_latency_ms=gate_ms,
                llm_called=llm_called,
            )
