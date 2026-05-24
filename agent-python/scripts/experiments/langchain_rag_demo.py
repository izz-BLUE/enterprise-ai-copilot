#!/usr/bin/env python3
"""
langchain_rag_demo.py —— LangChain RAG 最小 Demo

使用 LangChain 的 ChatPromptTemplate + ChatOpenAI 重建 RAG 问答链路，
复用现有 hybrid_retriever 做检索，对比手写 rag_service.py 的对应关系。

用法:
    python agent-python/scripts/experiments/langchain_rag_demo.py "病假需要提供哪些材料？"
"""

import os
import sys

# ── 路径 ──────────────────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, '..', '..', '..'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'agent-python'))

from app.core.config import (
    DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, DEEPSEEK_TEMPERATURE,
)
from app.retrieval.hybrid_retriever import retrieve

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

# ── RAG Prompt 模板 ───────────────────────────────────────
# 对应 app/prompts/system_prompt.py 中 build_rag_prompt 的逻辑
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
    "3. 如果知识库中没有明确答案，请明确说明"
    "\"当前知识库暂无相关信息\"，不要猜测或编造。\n"
)


def _build_context(chunks: list[dict]) -> str:
    """将检索结果拼接为上下文字符串（对应 build_rag_prompt 片段）。"""
    if not chunks:
        return "当前知识库未检索到相关内容。"

    sections = []
    for i, c in enumerate(chunks, 1):
        sections.append(
            f"【知识{i}】来源: {c['domain']}/{c['source_file']}\n"
            f"{c['content']}"
        )
    return "\n\n".join(sections)


def main():
    if len(sys.argv) < 2:
        print('用法: python agent-python/scripts/experiments/langchain_rag_demo.py "<问题>"')
        sys.exit(1)

    question = sys.argv[1]

    # 1. 检索（复用现有 hybrid_retriever）
    print(f'问题: {question}')
    topk = retrieve(question, top_k=3)

    if not topk:
        print('\n未检索到相关内容。')
        return

    chunk_ids = [c['id'] for c in topk]
    sources = sorted({c['source_file'] for c in topk})
    print(f'检索到 {len(topk)} 个 chunk: {chunk_ids}')
    print(f'来源: {sources}')

    # 2. 拼接 context（对应 build_rag_prompt 的逻辑）
    context = _build_context(topk)

    # 3. LangChain prompt 模板（对应手写 f-string 拼接）
    prompt = ChatPromptTemplate.from_messages([
        ("system", RAG_SYSTEM_TEMPLATE),
        ("user", "用户问题：{question}"),
    ])

    # 4. LangChain LLM 调用（对应 llm_service.call_llm）
    llm = ChatOpenAI(
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        model=DEEPSEEK_MODEL,
        temperature=DEEPSEEK_TEMPERATURE,
    )

    chain = prompt | llm
    response = chain.invoke({"context": context, "question": question})

    answer = response.content
    print(f'\n{"=" * 60}')
    print(f'回答:\n{answer}')
    print(f'{"=" * 60}')


if __name__ == '__main__':
    main()
