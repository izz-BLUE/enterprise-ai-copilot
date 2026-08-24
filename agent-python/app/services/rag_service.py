from uuid import uuid4

from app.core.config import DEEPSEEK_MODEL, logger
from app.guards.safety_guard import check_user_query_safety
from app.schemas.chat_schema import ChatResponse
from app.services.rag_answer_service import answer_rag


def process_chat(message: str, trace_id: str = '', top_k: int = 3,
                 retrieval_mode: str = 'hybrid',
                 rewrite_mode: str | None = None) -> ChatResponse:
    if not trace_id:
        trace_id = str(uuid4())

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

    result = answer_rag(
        message,
        trace_id=trace_id,
        top_k=top_k,
        retrieval_mode=retrieval_mode,
        rewrite_mode=rewrite_mode,
    )
    return ChatResponse(
        answer=result.answer,
        model=result.model,
        traceId=trace_id,
        success=result.success,
        sources=result.sources,
    )
