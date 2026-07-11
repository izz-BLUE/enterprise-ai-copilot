"""
langchain_rag_chain.py —— LangChain RAG Chain 封装

将检索 + Prompt 模板 + LLM 调用封装为可复用函数，
使用 LangChain ChatPromptTemplate + ChatOpenAI + LCEL。
此模块不替换 /agent/chat 主流程，仅作为实验性可复用封装。
"""

from time import perf_counter

from app.core.config import (
    DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL,
    DEEPSEEK_TEMPERATURE, LLM_TIMEOUT, logger,
)
from app.retrieval.hybrid_retriever import retrieve_with_signals
from app.retrieval.retrieval_gate import evaluate_gate_timed_fail_open, log_gate_event

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

# ── RAG Prompt 模板 ───────────────────────────────────────
RAG_SYSTEM_TEMPLATE = (
    "你是企业内部 AI 助手，必须严格基于【企业知识库内容】回答问题。\n"
    "\n"
    "以下是企业知识库内容：\n"
    "{context}\n"
    "\n"
    "回答规则（必须遵守）：\n"
    "1. 涉及材料清单、时间、金额、天数时，必须逐条使用知识库原文中的项目，"
    "不得用自己的常识补充或替换。\n"
    "2. 如果知识库中列出了多个材料或条件，必须尽量完整列出，不要遗漏。\n"
    "3. 如果知识库中包含时间范围（如上下班时间、午休时间），请完整列出所有时间点。\n"
    "4. 如果知识库中包含审批角色或流程节点，请尽量保留制度原文的表述。\n"
    "5. 如果知识库中没有明确答案，请明确说明"
    "\"当前知识库暂无相关信息\"，不要猜测或编造。\n"
)


def _build_context(chunks: list[dict]) -> str:
    """将检索结果拼接为上下文字符串。"""
    sections = []
    for i, c in enumerate(chunks, 1):
        sections.append(
            f"【知识{i}】来源: {c['domain']}/{c['source_file']}\n"
            f"{c['content']}"
        )
    return "\n\n".join(sections)


def answer_with_langchain_rag(
    question: str, top_k: int = 3, *, retrieval_query: str | None = None,
    trace_id: str = '',
) -> dict:
    """使用 LangChain RAG Chain 回答用户问题。

    返回 dict:
        answer, model, success, sources
    异常时 success=False，answer 为降级文案。
    """
    # 1. 共享 scored retrieval + Shadow gate；Prompt 仍使用原始 question。
    retrieval_started = perf_counter()
    chunks, candidate_signals = retrieve_with_signals(
        retrieval_query or question, top_k=top_k,
    )
    retrieval_latency_ms = (perf_counter() - retrieval_started) * 1000
    gate_decision, gate_latency_ms = evaluate_gate_timed_fail_open(
        candidate_signals, trace_id=trace_id or '-',
    )

    if not chunks:
        log_gate_event(
            trace_id=trace_id or '-', decision=gate_decision,
            candidate_count=len(candidate_signals),
            retrieval_latency_ms=retrieval_latency_ms,
            gate_latency_ms=gate_latency_ms, llm_called=False,
        )
        return {
            "answer": "当前知识库暂无相关信息",
            "model": DEEPSEEK_MODEL,
            "success": True,
            "sources": [],
        }

    # 2. 构造 context
    context = _build_context(chunks)

    # 3. LangChain prompt + LLM
    prompt = ChatPromptTemplate.from_messages([
        ("system", RAG_SYSTEM_TEMPLATE),
        ("user", "用户问题：{question}"),
    ])

    llm = ChatOpenAI(
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        model=DEEPSEEK_MODEL,
        temperature=DEEPSEEK_TEMPERATURE,
        timeout=LLM_TIMEOUT,
    )

    chain = prompt | llm

    try:
        response = chain.invoke({"context": context, "question": question})
    except Exception:
        logger.exception('[%s] LangChain LLM 调用失败', trace_id or '-')
        log_gate_event(
            trace_id=trace_id or '-', decision=gate_decision,
            candidate_count=len(candidate_signals),
            retrieval_latency_ms=retrieval_latency_ms,
            gate_latency_ms=gate_latency_ms, llm_called=True,
        )
        return {
            "answer": "当前 AI 服务暂时不可用，请稍后重试。",
            "model": DEEPSEEK_MODEL,
            "success": False,
            "sources": [],
        }

    log_gate_event(
        trace_id=trace_id or '-', decision=gate_decision,
        candidate_count=len(candidate_signals),
        retrieval_latency_ms=retrieval_latency_ms,
        gate_latency_ms=gate_latency_ms, llm_called=True,
    )

    # 4. 组装返回
    sources = [
        {"id": c["id"], "source_file": c["source_file"], "chunk_index": c["chunk_index"]}
        for c in chunks
    ]

    return {
        "answer": response.content,
        "model": DEEPSEEK_MODEL,
        "success": True,
        "sources": sources,
    }
