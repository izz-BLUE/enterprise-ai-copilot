"""兼容入口：生产 RAG 已统一到 RagAnswerService。

保留旧函数名，避免评测脚本和外部导入在一次改造中断裂；本模块不再构建第二套
Prompt、LLM Client 或 LangChain Chain。
"""

from app.prompts.system_prompt import SYSTEM_PROMPT
from app.services.rag_answer_service import answer_rag

RAG_SYSTEM_TEMPLATE = SYSTEM_PROMPT
RAG_USER_TEMPLATE = '【不可信用户问题】\n用户问题：{question}'


def answer_with_langchain_rag(
    question: str,
    top_k: int = 3,
    *,
    retrieval_query: str | None = None,
    trace_id: str = '',
) -> dict:
    result = answer_rag(
        question,
        trace_id=trace_id or '-',
        top_k=top_k,
        retrieval_query=retrieval_query,
    )
    return {
        'answer': result.answer,
        'model': result.model,
        'success': result.success,
        'sources': result.sources,
    }
