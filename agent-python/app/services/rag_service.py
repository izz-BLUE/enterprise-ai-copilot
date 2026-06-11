from uuid import uuid4

from app.core.config import DEEPSEEK_MODEL, logger
from app.prompts.system_prompt import SYSTEM_PROMPT, build_rag_prompt
from app.retrieval.hybrid_retriever import retrieve
from app.schemas.chat_schema import ChatResponse
from app.services.llm_service import call_llm


def process_chat(message: str, trace_id: str = '', top_k: int = 3) -> ChatResponse:
    if not trace_id:
        trace_id = str(uuid4())

    try:
        # 1. 检索
        chunks = retrieve(message, top_k=top_k)
        logger.info('[%s] 用户问题: %s | 命中 chunk: %d', trace_id, message, len(chunks))
        for c in chunks:
            logger.info('[%s]   - %s [%s] %s', trace_id, c['id'], c['domain'], c['source_file'])

        # 2. 拼接 RAG Prompt
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
