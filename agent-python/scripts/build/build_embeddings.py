#!/usr/bin/env python3
"""
build_embeddings.py — 使用 sentence-transformers 为知识库 chunk 生成 Embedding

用法:
    python agent-python/scripts/build_embeddings.py

输出:
    data/processed/embeddings.json
"""

import json
import os
import sys
import time

# ── 路径自动识别 ────────────────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, '..', '..', '..'))
CHUNKS_FILE = os.path.join(PROJECT_ROOT, 'data', 'processed', 'chunks.json')
OUTPUT_FILE = os.path.join(PROJECT_ROOT, 'data', 'processed', 'embeddings.json')

MODEL_NAME = 'BAAI/bge-small-zh-v1.5'
EXPECTED_DIM = 512  # bge-small-zh-v1.5 输出维度


def main():
    # 1. 读取 chunks
    if not os.path.isfile(CHUNKS_FILE):
        print(f'错误: 未找到 {CHUNKS_FILE}')
        print('请先运行 build_chunks.py 生成 chunks.json')
        sys.exit(1)

    with open(CHUNKS_FILE, 'r', encoding='utf-8') as f:
        chunks = json.load(f)

    if not chunks:
        print('chunks.json 为空，没有需要处理的文档。')
        sys.exit(0)

    print(f'读取到 {len(chunks)} 个 chunk，开始加载模型...')
    print(f'模型: {MODEL_NAME}')
    print('首次加载会下载模型（约 100MB），请保持网络畅通...\n')

    # 2. 加载模型（延迟导入，避免未安装时直接报错）
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        print('错误: 请先安装 sentence-transformers')
        print('  pip install sentence-transformers')
        sys.exit(1)

    t0 = time.time()
    model = SentenceTransformer(MODEL_NAME)
    print(f'模型加载完成，耗时 {time.time() - t0:.1f}s')

    # 3. 批量生成 embedding
    contents = [chunk['content'] for chunk in chunks]
    print(f'开始生成 {len(contents)} 个 chunk 的 embedding...')

    t1 = time.time()
    embeddings = model.encode(contents, normalize_embeddings=True, show_progress_bar=True)
    dim = embeddings.shape[1]
    print(f'生成完成，耗时 {time.time() - t1:.1f}s')
    print(f'Embedding 维度: {dim}')

    # 4. 组装输出
    results = []
    for i, chunk in enumerate(chunks):
        results.append({
            'id': chunk['id'],
            'domain': chunk['domain'],
            'source_file': chunk['source_file'],
            'chunk_index': chunk['chunk_index'],
            'content': chunk['content'],
            'embedding': embeddings[i].tolist(),
        })

    # 5. 写入文件
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False)

    print(f'\n处理完成！')
    print(f'  文档数: {len(chunks)}')
    print(f'  Embedding 维度: {dim}')
    print(f'  输出文件: {OUTPUT_FILE}')


if __name__ == '__main__':
    main()
