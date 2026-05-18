#!/usr/bin/env python3
"""
semantic_retrieval.py — 基于 Embedding + Cosine Similarity 的语义检索

从 data/processed/embeddings.json 加载 embedding，
使用用户问题生成 embedding，计算 cosine similarity，返回 topK。

用法:
    python agent-python/scripts/semantic_retrieval.py "休三天假怎么审批"

依赖:
    sentence-transformers
    numpy
"""

import json
import os
import sys
import time

import numpy as np

# ── 路径自动识别 ────────────────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, '..', '..'))
EMBEDDINGS_FILE = os.path.join(PROJECT_ROOT, 'data', 'processed', 'embeddings.json')

TOP_K = 3
MODEL_NAME = 'BAAI/bge-small-zh-v1.5'


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """计算两个向量的余弦相似度。"""
    dot = np.dot(a, b)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(dot / (norm_a * norm_b))


def main():
    # 1. 加载 embeddings
    if not os.path.isfile(EMBEDDINGS_FILE):
        print(f'错误: 未找到 {EMBEDDINGS_FILE}')
        print('请先运行 build_embeddings.py 生成 embedding')
        sys.exit(1)

    with open(EMBEDDINGS_FILE, 'r', encoding='utf-8') as f:
        chunks = json.load(f)

    if not chunks:
        print('embeddings.json 为空')
        sys.exit(1)

    # 提取 embedding 矩阵和 metadata
    embeddings = np.array([c['embedding'] for c in chunks], dtype=np.float32)
    dim = embeddings.shape[1]
    print(f'Embedding 维度: {dim}')
    print(f'共 {len(chunks)} 个 chunk\n')

    # 2. 读取用户问题
    if len(sys.argv) < 2:
        print('用法: python agent-python/scripts/semantic_retrieval.py "<问题>"')
        sys.exit(1)

    query = sys.argv[1]

    # 3. 加载模型并生成查询 embedding
    print(f'加载模型: {MODEL_NAME}')
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        print('错误: 请先安装 sentence-transformers')
        print('  pip install sentence-transformers')
        sys.exit(1)

    t0 = time.time()
    model = SentenceTransformer(MODEL_NAME)
    print(f'模型加载完成，耗时 {time.time() - t0:.1f}s')

    t1 = time.time()
    query_emb = model.encode(query, normalize_embeddings=True)
    print(f'查询 embedding 生成完成，耗时 {time.time() - t1:.3f}s\n')

    # 4. 计算所有 cosine similarity
    scores = []
    for i, chunk in enumerate(chunks):
        sim = cosine_similarity(query_emb, embeddings[i])
        scores.append((sim, chunk))

    # 按 similarity 降序排列
    scores.sort(key=lambda x: -x[0])

    # 5. 输出 topK
    print(f'问题: {query}')
    print(f'Top {TOP_K} 结果:\n')

    for i, (score, chunk) in enumerate(scores[:TOP_K], 1):
        print(f'[{i}] similarity={score:.4f}')
        print(f'    id:          {chunk["id"]}')
        print(f'    domain:      {chunk["domain"]}')
        print(f'    source_file: {chunk["source_file"]}')
        content_preview = chunk['content'][:120]
        print(f'    content:     {content_preview}{"..." if len(chunk["content"]) > 120 else ""}')
        print()


if __name__ == '__main__':
    main()
