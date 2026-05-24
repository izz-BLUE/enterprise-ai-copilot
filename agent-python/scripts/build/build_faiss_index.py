#!/usr/bin/env python3
"""
build_faiss_index.py — 使用 Faiss 构建向量索引

从 data/processed/embeddings.json 读取 embedding，
构建 Faiss IndexFlatIP 索引并保存到磁盘。

用法:
    python agent-python/scripts/build_faiss_index.py

输出:
    data/processed/faiss.index        — Faiss 索引文件
    data/processed/faiss_metadata.json — 索引位置 → chunk metadata 映射
"""

import json
import os
import sys
import time

import numpy as np

# ── 路径自动识别 ────────────────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, '..', '..', '..'))
EMBEDDINGS_FILE = os.path.join(PROJECT_ROOT, 'data', 'processed', 'embeddings.json')
FAISS_INDEX_FILE = os.path.join(PROJECT_ROOT, 'data', 'processed', 'faiss.index')
FAISS_META_FILE = os.path.join(PROJECT_ROOT, 'data', 'processed', 'faiss_metadata.json')


def main():
    try:
        import faiss
    except ImportError:
        print('错误: 请先安装 faiss-cpu')
        print('  pip install faiss-cpu')
        sys.exit(1)

    # 1. 读取 embeddings
    if not os.path.isfile(EMBEDDINGS_FILE):
        print(f'错误: 未找到 {EMBEDDINGS_FILE}')
        print('请先运行 build_embeddings.py 生成 embedding')
        sys.exit(1)

    with open(EMBEDDINGS_FILE, 'r', encoding='utf-8') as f:
        chunks = json.load(f)

    if not chunks:
        print('embeddings.json 为空')
        sys.exit(0)

    print(f'读取到 {len(chunks)} 条 embedding 记录')
    print(f'Embedding 维度: {len(chunks[0]["embedding"])}')

    # 2. 提取向量矩阵 (float32)
    embeddings = np.array([c['embedding'] for c in chunks], dtype=np.float32)
    dim = embeddings.shape[1]

    # ── 为什么要对 embedding 做 L2 normalize？ ─────────────────
    #
    # cosine_similarity(A, B) = (A·B) / (|A| × |B|)
    #
    # 如果 A 和 B 都已经归一化（|A| = |B| = 1），那么：
    #
    #     cosine_similarity(A, B) = A · B   (= inner product)
    #
    # 也就是说：
    # L2 normalize 后，inner product 和 cosine similarity 是等价的。
    #
    # Faiss 的 IndexFlatIP 做的就是 inner product 搜索。
    # 所以我们先 normalize，然后直接使用 IndexFlatIP，
    # 搜索结果就是 cosine similarity。
    #
    # 注意：normalize_embeddings=True 已经保证 embeddings 是归一化的，
    # 但这里显式做一次 L2 normalize 确保一致性。
    faiss.normalize_L2(embeddings)

    # 3. 构建 IndexFlatIP
    #
    # ── IndexFlatIP 的作用 ─────────────────────────────────────
    #
    # IndexFlatIP 是 Faiss 中最基础的索引类型。
    # "Flat" 表示不压缩、不量化，完整保留所有向量。
    # "IP" 表示 Inner Product（内积），即点积。
    #
    # 搜索时做的事情：
    #   1. 计算查询向量与索引中每个向量的内积（点积）
    #   2. 按内积降序排列
    #   3. 返回 TopK 结果
    #
    # 注意：IndexFlatIP 是 "精确" 的，不是近似搜索。
    # 它和手动 for 循环算 cosine similarity 的结果完全一样。
    # 差别在于 Faiss 内部使用高度优化的 BLAS 库（多线程、SIMD 指令集），
    # 在向量数量大时比 Python 循环快很多个数量级。
    #
    # ── 为什么 Faiss 比全量 Python cosine 更适合大规模 retrieval？ ──
    #
    # 1. 性能：Faiss 底层用 C++ / BLAS 实现，利用 SIMD 和多核并行。
    #    在百万级向量上，Faiss 比 Python for 循环快 100～1000 倍。
    #
    # 2. 进阶支持：IndexFlatIP 虽仍是全量搜索，但 Faiss 提供了
    #    IVF（倒排文件）、PQ（乘积量化）等近似索引，
    #    可以在亿级规模下把搜索时间降到毫秒级，精度损失很小。
    #
    # 3. 内存管理：Faiss 支持 mmap 映射索引文件，不占用进程内存。
    #
    # 当前只有 6 个 chunk，全量搜索已经很快。
    # 用 Faiss 的核心目的是为后续海量知识库做准备——代码不需要改，
    # 只需要把 IndexFlatIP 换成 IVF/IndexIVFFlat 即可扩展到百万级。
    #
    # ───────────────────────────────────────────────────────────

    index = faiss.IndexFlatIP(dim)

    print(f'\n构建 Faiss Index...')
    t0 = time.time()
    index.add(embeddings)
    print(f'Index 构建完成，耗时 {time.time() - t0:.3f}s')

    print(f'\n  Index 类型: {type(index).__name__}')
    print(f'  向量数量:   {index.ntotal}')
    print(f'  向量维度:   {dim}')

    # 4. 保存 Faiss 索引
    # 注：faiss.write_index 在 Windows 上不支持包含中文的路径，
    # 所以先序列化为 bytes，再用 Python 写入文件。
    os.makedirs(os.path.dirname(FAISS_INDEX_FILE), exist_ok=True)
    buf = faiss.serialize_index(index)
    with open(FAISS_INDEX_FILE, 'wb') as f:
        f.write(buf)
    print(f'  index 文件:  {FAISS_INDEX_FILE}')

    # 5. 保存 metadata 映射（只保留 metadata，不保留 embedding）
    metadata = []
    for c in chunks:
        metadata.append({
            'id': c['id'],
            'domain': c['domain'],
            'source_file': c['source_file'],
            'chunk_index': c['chunk_index'],
            'content': c['content'],
        })

    with open(FAISS_META_FILE, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    print(f'  metadata 文件: {FAISS_META_FILE}')
    print(f'\n完成！')


if __name__ == '__main__':
    main()
