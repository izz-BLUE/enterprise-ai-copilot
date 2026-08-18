from uuid import uuid4
from time import perf_counter

from app.core.config import DEEPSEEK_MODEL, REWRITE_MODE, logger
from app.guards.safety_guard import check_user_query_safety
from app.prompts.system_prompt import SYSTEM_PROMPT, build_rag_prompt
from app.retrieval.hybrid_retriever import retrieve_with_signals
from app.retrieval.query_rewriter import rewrite_query
from app.retrieval.retrieval_gate import evaluate_gate_timed_fail_open, log_gate_event
from app.schemas.chat_schema import ChatResponse
from app.services.llm_service import call_llm


def process_chat(message: str, trace_id: str = '', top_k: int = 3,
                 retrieval_mode: str = 'hybrid',
                 rewrite_mode: str | None = None) -> ChatResponse:
    if not trace_id:
        trace_id = str(uuid4())

    if rewrite_mode is None:
        rewrite_mode = REWRITE_MODE

    # 0. Safety Guard 前置检查（高风险问题直接拒答，不进入检索 / LLM）
    safety_result = check_user_query_safety(message)
    if not safety_result['safe']:
        logger.info('[%s] Safety Guard 拦截: category=%s, reason=%s',
                     trace_id, safety_result['category'], safety_result['reason'])
        return ChatResponse(
            answer=safety_result['message'],
            model=DEEPSEEK_MODEL,
            traceId=trace_id,
            success=True,
        )

    gate_context = None
    llm_called = False
    try:
        # 1. Query Rewrite（只改写检索用 query，不改 original_query）
        rewrite_result = rewrite_query(message, mode=rewrite_mode)
        retrieval_query = rewrite_result['rewritten_query']
        if rewrite_result['rewrite_applied']:
            logger.info('[%s] Query rewrite applied reason=%s',
                        trace_id, rewrite_result['rewrite_reason'])

        # 2. 带原始分数的检索 + Shadow gate（不改变 chunks 和生成行为）
        retrieval_started = perf_counter()
        chunks, candidate_signals = retrieve_with_signals(
            retrieval_query, top_k=top_k, mode=retrieval_mode,
        )
        retrieval_latency_ms = (perf_counter() - retrieval_started) * 1000
        gate_decision, gate_latency_ms = evaluate_gate_timed_fail_open(
            candidate_signals, trace_id=trace_id,
        )
        gate_context = (
            gate_decision, len(candidate_signals),
            retrieval_latency_ms, gate_latency_ms,
        )
        logger.info('[%s] Retrieval completed mode=%s chunk_count=%d',
                    trace_id, retrieval_mode, len(chunks))

        # 3. 拼接 RAG Prompt（使用 original_query）
        user_prompt = build_rag_prompt(message, chunks)

        # 4. 调用 LLM
        logger.info('[%s] 开始调用 LLM', trace_id)
        llm_called = True
        answer = call_llm(SYSTEM_PROMPT, user_prompt)

        # Model Reliability P0：Provider 偶发空响应在应用层内部重试 1 次；
        # Provider 网络错误（LLMProviderError）不额外重试，直接抛给外层兜底。
        if not (answer or '').strip():
            logger.warning('[%s] LLM 首次返回空响应，进行 1 次内部重试', trace_id)
            answer = call_llm(SYSTEM_PROMPT, user_prompt)
            if not (answer or '').strip():
                # 重试仍空：明确走服务不可用兜底，避免返回空答案给用户。
                logger.warning('[%s] LLM 重试后仍返回空响应，走失败兜底', trace_id)
                return ChatResponse(
                    answer='当前 AI 服务暂时不可用，请稍后重试。',
                    model=DEEPSEEK_MODEL,
                    traceId=trace_id,
                    success=False,
                )

        logger.info('[%s] LLM 调用完成', trace_id)

        return ChatResponse(
            answer=answer,
            model=DEEPSEEK_MODEL,
            traceId=trace_id,
            success=True,
        )
    except Exception:
        logger.exception('[%s] 调用 LLM 失败', trace_id)
        return ChatResponse(
            answer='当前 AI 服务暂时不可用，请稍后重试。',
            model=DEEPSEEK_MODEL,
            traceId=trace_id,
            success=False,
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
