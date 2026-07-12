#!/usr/bin/env python3
"""
build_embeddings.py — 使用 sentence-transformers 为知识库 chunk 生成 Embedding

用法:
    python scripts/build/build_embeddings.py
    python scripts/build/build_embeddings.py --backend onnx --model-path models/embedding/bge-small-zh-v1.5-onnx
    python scripts/build/build_embeddings.py --output /tmp/embeddings.json

输出:
    data/processed/embeddings.json（默认）
"""

import argparse
import json
import os
import sys
import time

import numpy as np

# ── 路径自动识别 ────────────────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, '..', '..', '..'))
CHUNKS_FILE = os.path.join(PROJECT_ROOT, 'data', 'processed', 'chunks.json')
DEFAULT_OUTPUT_FILE = os.path.join(PROJECT_ROOT, 'data', 'processed', 'embeddings.json')

DEFAULT_MODEL_NAME = 'BAAI/bge-small-zh-v1.5'
EXPECTED_DIM = 512  # bge-small-zh-v1.5 输出维度


def _load_model(backend: str, model_name: str, model_path: str, onnx_file: str):
    """根据后端配置加载 SentenceTransformer 模型。"""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        print('错误: 请先安装 sentence-transformers')
        sys.exit(1)

    if backend == 'onnx':
        source = model_path if model_path else model_name
        if model_path and not os.path.isdir(model_path):
            print(f'错误: 模型目录不存在: {model_path}')
            sys.exit(1)
        onnx_path = os.path.join(source, onnx_file) if model_path else None
        if onnx_path and not os.path.isfile(onnx_path):
            print(f'错误: ONNX 文件不存在: {onnx_path}')
            sys.exit(1)
        model = SentenceTransformer(
            source,
            backend='onnx',
            model_kwargs={
                'provider': 'CPUExecutionProvider',
                'file_name': onnx_file,
                'export': False,
            },
        )
    else:
        model = SentenceTransformer(model_name)

    return model


def main():
    parser = argparse.ArgumentParser(description='为知识库 chunk 生成 Embedding')
    parser.add_argument('--backend', type=str, default='torch', choices=['torch', 'onnx'],
                        help='推理后端: torch | onnx（默认 torch）')
    parser.add_argument('--model-name', type=str, default=DEFAULT_MODEL_NAME,
                        help=f'模型名称（默认 {DEFAULT_MODEL_NAME}）')
    parser.add_argument('--model-path', type=str, default='',
                        help='本地模型目录（ONNX 模式可选）')
    parser.add_argument('--onnx-file', type=str, default='onnx/model.onnx',
                        help='ONNX 文件名（默认 onnx/model.onnx）')
    parser.add_argument('--output', type=str, default='',
                        help=f'输出文件路径（默认 {DEFAULT_OUTPUT_FILE}）')
    args = parser.parse_args()

    output_file = args.output if args.output else DEFAULT_OUTPUT_FILE

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

    print(f'读取到 {len(chunks)} 个 chunk')
    print(f'后端: {args.backend}')
    print(f'模型: {args.model_name}')
    if args.model_path:
        print(f'模型路径: {args.model_path}')

    # 2. 加载模型
    t0 = time.time()
    model = _load_model(args.backend, args.model_name, args.model_path, args.onnx_file)
    print(f'模型加载完成，耗时 {time.time() - t0:.1f}s')

    # 3. 批量生成 embedding
    contents = [chunk['content'] for chunk in chunks]
    print(f'开始生成 {len(contents)} 个 chunk 的 embedding...')

    t1 = time.time()
    embeddings = model.encode(contents, normalize_embeddings=True, show_progress_bar=True)
    embeddings = np.asarray(embeddings, dtype=np.float32)
    dim = embeddings.shape[1]
    print(f'生成完成，耗时 {time.time() - t1:.1f}s')
    print(f'Embedding 维度: {dim}')

    if dim != EXPECTED_DIM:
        print(f'错误: 期望维度 {EXPECTED_DIM}，实际 {dim}')
        sys.exit(1)

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
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False)

    print(f'\n处理完成！')
    print(f'  文档数: {len(chunks)}')
    print(f'  Embedding 维度: {dim}')
    print(f'  后端: {args.backend}')
    print(f'  输出文件: {output_file}')


if __name__ == '__main__':
    main()
