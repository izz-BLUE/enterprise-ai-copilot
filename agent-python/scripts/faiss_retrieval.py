#!/usr/bin/env python3
"""
faiss_retrieval.py — 基于 Faiss Index 的向量检索

从 data/processed/faiss.index 加载索引，
用户问题生成 embedding 后使用 index.search() 检索 TopK。

用法:
    python agent-python/scripts/faiss_retrieval.py "休三天假怎么审批"
"""

import json
import os
import sys
import time

import numpy as np

# ── 路径自动识别 ────────────────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, '..', '..'))
FAISS_INDEX_FILE = os.path.join(PROJECT_ROOT, 'data', 'processed', 'faiss.index')
FAISS_META_FILE = os.path.join(PROJECT_ROOT, 'data', 'processed', 'faiss_metadata.json')

TOP_K = 3
MODEL_NAME = 'BAAI/bge-small-zh-v1.5'


def main():
    # 1. 加载 Faiss Index
    try:
        import faiss
    except ImportError:
        print('错误: 请先安装 faiss-cpu')
        print('  pip install faiss-cpu')
        sys.exit(1)

    if not os.path.isfile(FAISS_INDEX_FILE):
        print(f'错误: 未找到 {FAISS_INDEX_FILE}')
        print('请先运行 build_faiss_index.py 构建索引')
        sys.exit(1)

    with open(FAISS_INDEX_FILE, 'rb') as f:
        buf = f.read()
    index = faiss.deserialize_index(np.frombuffer(buf, dtype='uint8'))
    dim = index.d
    print(f'Index 类型: {type(index).__name__}')
    print(f'向量数量:   {index.ntotal}')
    print(f'向量维度:   {dim}')

    # 2. 加载 metadata
    if not os.path.isfile(FAISS_META_FILE):
        print(f'错误: 未找到 {FAISS_META_FILE}')
        sys.exit(1)

    with open(FAISS_META_FILE, 'r', encoding='utf-8') as f:
        metadata = json.load(f)

    print(f'Metadata 记录数: {len(metadata)}\n')

    # 3. 读取用户问题
    if len(sys.argv) < 2:
        print('用法: python agent-python/scripts/faiss_retrieval.py "<问题>"')
        sys.exit(1)

    query = sys.argv[1]

    # 4. 加载模型并生成查询 embedding
    print(f'加载模型: {MODEL_NAME}')
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        print('错误: 请先安装 sentence-transformers')
        sys.exit(1)

    t0 = time.time()
    model = SentenceTransformer(MODEL_NAME)
    print(f'模型加载完成，耗时 {time.time() - t0:.1f}s')

    t1 = time.time()
    query_emb = model.encode(query, normalize_embeddings=True)
    print(f'查询 embedding 生成完成，耗时 {time.time() - t1:.3f}s')

    # ── 为什么可以这样搜？ ──────────────────────────────────────
    #
    # IndexFlatIP 搜索 = 计算查询向量与所有索引向量的内积，取 TopK。
    #
    # build_faiss_index.py 中已经对所有向量做了 L2 normalize，
    # 所以 index.search() 返回的内积 = cosine similarity。
    #
    # 这里再将查询向量 reshape 为 (1, dim) 的二维矩阵，
    # 因为 Faiss search() 要求输入形状为 (n_queries, dim)。
    #
    # ───────────────────────────────────────────────────────────

    query_emb = np.expand_dims(query_emb, axis=0).astype(np.float32)
    faiss.normalize_L2(query_emb)

    # 5. 检索
    t2 = time.time()
    distances, indices = index.search(query_emb, TOP_K)
    print(f'Faiss search 耗时: {time.time() - t2:.4f}s\n')

    # distances:  shape (1, TOP_K)   — 每个结果的相似度
    # indices:    shape (1, TOP_K)   — 每个结果在索引中的位置

    # 6. 输出结果
    print(f'问题: {query}')
    print(f'Top {TOP_K} 结果:\n')

    for i in range(TOP_K):
        idx = indices[0][i]
        score = float(distances[0][i])
        chunk = metadata[idx]

        content_preview = chunk['content'][:120]
        print(f'[{i + 1}] similarity={score:.4f}')
        print(f'    id:          {chunk["id"]}')
        print(f'    domain:      {chunk["domain"]}')
        print(f'    source_file: {chunk["source_file"]}')
        print(f'    content:     {content_preview}{"..." if len(chunk["content"]) > 120 else ""}')
        print()


if __name__ == '__main__':
    main()
