from uuid import uuid4

from app.core.config import DEEPSEEK_MODEL, logger
from app.prompts.system_prompt import SYSTEM_PROMPT, build_rag_prompt
from app.retrieval.faiss_retriever import retrieve
from app.schemas.chat_schema import ChatResponse
from app.services.llm_service import call_llm


def process_chat(message: str) -> ChatResponse:
    try:
        # 1. 检索
        chunks = retrieve(message)
        logger.info('用户问题: %s | 命中 chunk: %d', message, len(chunks))
        for c in chunks:
            logger.info('  - %s [%s] %s', c['id'], c['domain'], c['source_file'])

        # 2. 拼接 RAG Prompt
        user_prompt = build_rag_prompt(message, chunks)

        # 3. 调用 LLM
        answer = call_llm(SYSTEM_PROMPT, user_prompt)

        return ChatResponse(
            answer=answer,
            model=DEEPSEEK_MODEL,
            traceId=str(uuid4()),
            success=True,
        )
    except Exception:
        logger.exception('调用 LLM 失败')
        return ChatResponse(
            answer='当前 AI 服务暂时不可用，请稍后重试。',
            model=DEEPSEEK_MODEL,
            traceId=str(uuid4()),
            success=False,
        )
