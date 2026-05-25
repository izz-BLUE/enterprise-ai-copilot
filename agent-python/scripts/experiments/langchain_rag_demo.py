#!/usr/bin/env python3
"""
langchain_rag_demo.py —— LangChain RAG Demo

调用 app.chains.langchain_rag_chain.answer_with_langchain_rag() 执行问答。

用法:
    python agent-python/scripts/experiments/langchain_rag_demo.py "病假需要提供哪些材料？"
"""

import os
import sys

# ── 路径 ──────────────────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, '..', '..', '..'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'agent-python'))

from app.chains.langchain_rag_chain import answer_with_langchain_rag


def main():
    if len(sys.argv) < 2:
        print('用法: python agent-python/scripts/experiments/langchain_rag_demo.py "<问题>"')
        sys.exit(1)

    question = sys.argv[1]
    print(f'问题: {question}')

    result = answer_with_langchain_rag(question)

    if result["sources"]:
        chunk_ids = [s["id"] for s in result["sources"]]
        files = sorted({s["source_file"] for s in result["sources"]})
        print(f'检索到 {len(result["sources"])} 个 chunk: {chunk_ids}')
        print(f'来源: {files}')

    print(f'\n{"=" * 60}')
    print(f'回答:\n{result["answer"]}')
    print(f'{"=" * 60}')


if __name__ == '__main__':
    main()
