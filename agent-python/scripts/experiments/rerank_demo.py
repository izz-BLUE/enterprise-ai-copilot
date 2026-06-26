#!/usr/bin/env python3
"""
rerank_demo.py — Cross Encoder Re-rank 演示脚本

输入一个问题，对比 Hybrid 排序与 Re-rank 后排序的变化，
展示 rerank_score 和两个阶段的耗时。

用法:
    uv run python scripts/experiments/rerank_demo.py "病假需要提供哪些材料？"
"""

import os
import sys
import time

# ── 路径自动识别 ────────────────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, '..', '..', '..'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'agent-python'))

from app.core.config import RERANK_CANDIDATE_K, RERANK_MODEL, logger  # noqa: E402
from app.retrieval.hybrid_retriever import (  # noqa: E402
    retrieve_hybrid,
    retrieve_hybrid_rerank,
)


def main():
    query = sys.argv[1] if len(sys.argv) > 1 else '病假需要提供哪些材料？'
    candidate_k = RERANK_CANDIDATE_K
    top_k = 3

    print('=' * 70)
    print('  Cross Encoder Re-rank 演示')
    print('=' * 70)
    print(f'  问题: {query}')
    print(f'  候选数量: {candidate_k}')
    print(f'  最终返回: {top_k}')
    print(f'  Re-rank 模型: {RERANK_MODEL}')
    print()

    # ── 阶段 1：Hybrid Retrieval ──────────────────────────────────
    print('-' * 70)
    print('  阶段 1：Hybrid Retrieval（Faiss + BM25 + RRF）')
    print('-' * 70)

    t0 = time.time()
    hybrid_results = retrieve_hybrid(query, top_k=candidate_k, candidate_k=candidate_k)
    hybrid_time = time.time() - t0

    print(f'  耗时: {hybrid_time:.3f}s')
    print(f'  返回 {len(hybrid_results)} 个候选:')
    print()
    print(f'  {"排名":<4}  {"Chunk ID":<12}  {"来源":<40}')
    print(f'  {"----":<4}  {"--------":<12}  {"----":<40}')
    for i, c in enumerate(hybrid_results, 1):
        source = c.get('source_file', '')
        if len(source) > 38:
            source = source[:35] + '...'
        print(f'  {i:<4}  {c["id"]:<12}  {source:<40}')
    print()

    # ── 阶段 2：Cross Encoder Re-rank ─────────────────────────────
    print('-' * 70)
    print('  阶段 2：Cross Encoder Re-rank')
    print('-' * 70)

    t0 = time.time()
    reranked = retrieve_hybrid_rerank(query, top_k=top_k, candidate_k=candidate_k)
    rerank_time = time.time() - t0

    print(f'  耗时: {rerank_time:.3f}s（含 Hybrid 召回 + Re-rank 精排）')
    print(f'  返回 {len(reranked)} 个结果:')
    print()
    print(f'  {"排名":<4}  {"Chunk ID":<12}  {"rerank_score":<14}  {"来源":<40}')
    print(f'  {"----":<4}  {"--------":<12}  {"------------":<14}  {"----":<40}')
    for i, c in enumerate(reranked, 1):
        source = c.get('source_file', '')
        if len(source) > 38:
            source = source[:35] + '...'
        score = c.get('rerank_score', 0.0)
        print(f'  {i:<4}  {c["id"]:<12}  {score:<14.4f}  {source:<40}')
    print()

    # ── 排名变化对比 ──────────────────────────────────────────────
    print('-' * 70)
    print('  排名变化对比')
    print('-' * 70)

    hybrid_ids = [c['id'] for c in hybrid_results[:top_k]]
    reranked_ids = [c['id'] for c in reranked]

    print(f'  Hybrid Top{top_k}:  {hybrid_ids}')
    print(f'  Re-rank Top{top_k}: {reranked_ids}')

    if hybrid_ids == reranked_ids:
        print('  结果: 排名未变化')
    else:
        changed = []
        for i, (hid, rid) in enumerate(zip(hybrid_ids, reranked_ids), 1):
            if hid != rid:
                changed.append(f'第{i}名: {hid} → {rid}')
        print(f'  排名变化: {changed}')
    print()

    # ── 耗时总结 ──────────────────────────────────────────────────
    print('-' * 70)
    print('  耗时总结')
    print('-' * 70)
    print(f'  Hybrid Retrieval: {hybrid_time:.3f}s')
    print(f'  Hybrid + Re-rank: {rerank_time:.3f}s')
    print(f'  Re-rank 额外开销: {rerank_time - hybrid_time:.3f}s')
    print()


if __name__ == '__main__':
    main()
