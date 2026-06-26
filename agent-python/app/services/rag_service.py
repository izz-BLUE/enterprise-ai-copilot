from uuid import uuid4

from app.core.config import DEEPSEEK_MODEL, REWRITE_MODE, logger
from app.prompts.system_prompt import SYSTEM_PROMPT, build_rag_prompt
from app.retrieval.hybrid_retriever import retrieve
from app.retrieval.query_rewriter import rewrite_query
from app.schemas.chat_schema import ChatResponse
from app.services.llm_service import call_llm


def process_chat(message: str, trace_id: str = '', top_k: int = 3,
                 retrieval_mode: str = 'hybrid',
                 rewrite_mode: str | None = None) -> ChatResponse:
    if not trace_id:
        trace_id = str(uuid4())

    if rewrite_mode is None:
        rewrite_mode = REWRITE_MODE

    try:
        # 0. Query Rewrite（只改写检索用 query，不改 original_query）
        rewrite_result = rewrite_query(message, mode=rewrite_mode)
        retrieval_query = rewrite_result['rewritten_query']
        if rewrite_result['rewrite_applied']:
            logger.info('[%s] Query rewrite: "%s" → "%s" (reason: %s)',
                        trace_id, message, retrieval_query,
                        rewrite_result['rewrite_reason'])

        # 1. 检索（使用 rewritten_query）
        chunks = retrieve(retrieval_query, top_k=top_k, mode=retrieval_mode)
        logger.info('[%s] 用户问题: %s | 检索 query: %s | 命中 chunk: %d',
                    trace_id, message, retrieval_query, len(chunks))
        for c in chunks:
            logger.info('[%s]   - %s [%s] %s', trace_id, c['id'], c['domain'], c['source_file'])

        # 2. 拼接 RAG Prompt（使用 original_query）
        user_prompt = build_rag_prompt(message, chunks)

        # 3. 调用 LLM
        logger.info('[%s] 开始调用 LLM', trace_id)
        answer = call_llm(SYSTEM_PROMPT, user_prompt)
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
