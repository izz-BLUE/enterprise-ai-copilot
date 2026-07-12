#!/usr/bin/env python3
"""
memtest.py — 测量 Embedding 模型各阶段内存占用

用法:
    EMBEDDING_BACKEND=torch python scripts/build/memtest.py
    EMBEDDING_BACKEND=onnx_st EMBEDDING_MODEL_PATH=... python scripts/build/memtest.py
    EMBEDDING_BACKEND=onnx_direct EMBEDDING_MODEL_PATH=... python scripts/build/memtest.py
"""

import gc
import os
import sys
import time

import psutil


def get_rss_mb() -> float:
    """获取当前进程 RSS (MB)"""
    return psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024


def get_uss_mb() -> float:
    """获取当前进程 USS (MB)，仅 Linux"""
    try:
        return psutil.Process(os.getpid()).memory_full_info().uss / 1024 / 1024
    except Exception:
        return -1.0


def report(stage: str) -> dict:
    gc.collect()
    time.sleep(0.5)
    gc.collect()
    return {
        'stage': stage,
        'rss_mb': round(get_rss_mb(), 1),
        'uss_mb': round(get_uss_mb(), 1),
    }


SAMPLES = [
    '公司上班时间是什么？',
    '几点上班？',
    '病假需要提供哪些材料？',
    '请病假要啥？',
    'VPN 怎么申请？',
    '新员工入职需要做什么？',
    '公司股票期权怎么分配？',
    '可以远程办公几天？',
]


def main():
    backend = os.getenv('EMBEDDING_BACKEND', 'torch')
    model_path = os.getenv('EMBEDDING_MODEL_PATH', '')
    model_name = os.getenv('EMBEDDING_MODEL_NAME', 'BAAI/bge-small-zh-v1.5')

    print(f'Backend: {backend}')
    print(f'Model: {model_name}')
    print(f'Path: {model_path or "(HuggingFace)"}')
    print()

    # 检查运行时模块
    print('Runtime check:')
    print(f'  torch in modules: {"torch" in sys.modules}')
    print(f'  sentence_transformers in modules: {"sentence_transformers" in sys.modules}')
    print()

    # 1. 启动后基线
    r0 = report('1. 启动后基线')

    # 2. 加载模型
    t_load = time.time()

    if backend == 'onnx_direct':
        # Direct ONNX: 不加载 torch
        sys.path.insert(0, '.')
        from app.retrieval.direct_onnx_embedding import encode as direct_encode, load_model as direct_load
        direct_load()
        encode_fn = lambda texts, **kw: direct_encode(texts, normalize=kw.get('normalize_embeddings', True))
    else:
        # SentenceTransformer 路径
        from sentence_transformers import SentenceTransformer
        if backend == 'onnx_st':
            source = model_path if model_path else model_name
            model = SentenceTransformer(
                source,
                backend='onnx',
                model_kwargs={
                    'provider': 'CPUExecutionProvider',
                    'file_name': 'onnx/model.onnx',
                    'export': False,
                },
            )
        else:
            model = SentenceTransformer(model_name)
        encode_fn = lambda texts, **kw: model.encode(texts, **kw)

    load_time = time.time() - t_load
    r1 = report('2. 模型加载后')

    # 检查加载后模块
    print('After load:')
    print(f'  torch in modules: {"torch" in sys.modules}')
    print(f'  sentence_transformers in modules: {"sentence_transformers" in sys.modules}')
    print()

    # 3. 第一次 encode
    t_first = time.time()
    _ = encode_fn(SAMPLES[0], normalize_embeddings=True)
    first_time = time.time() - t_first
    r2 = report('3. 第一次 encode 后')

    # 4. 连续 10 次
    times_10 = []
    for _ in range(10):
        t0 = time.time()
        encode_fn(SAMPLES[0], normalize_embeddings=True)
        times_10.append(time.time() - t0)
    r3 = report('4. 连续 10 次后')

    # 5. 连续 50 次
    times_50 = []
    for _ in range(50):
        t0 = time.time()
        encode_fn(SAMPLES[0], normalize_embeddings=True)
        times_50.append(time.time() - t0)
    r4 = report('5. 连续 50 次后')

    # 6. 批量 encode
    t_batch = time.time()
    _ = encode_fn(SAMPLES, normalize_embeddings=True)
    batch_time = time.time() - t_batch
    r5 = report('6. 批量 encode 后')

    # 输出
    print('=' * 60)
    print(f'  {backend.upper()} 内存基准')
    print('=' * 60)
    for r in [r0, r1, r2, r3, r4, r5]:
        print(f'  {r["stage"]:20s}  RSS={r["rss_mb"]:7.1f} MB  USS={r["uss_mb"]:7.1f} MB')

    max_rss = max(r['rss_mb'] for r in [r0, r1, r2, r3, r4, r5])
    max_uss = max(r['uss_mb'] for r in [r0, r1, r2, r3, r4, r5])
    delta = r5['rss_mb'] - r0['rss_mb']

    print()
    print(f'  加载时间:        {load_time:.2f}s')
    print(f'  首次 encode:     {first_time*1000:.1f}ms')
    print(f'  后续平均(10次):  {sum(times_10)/len(times_10)*1000:.1f}ms')
    print(f'  后续平均(50次):  {sum(times_50)/len(times_50)*1000:.1f}ms')
    print(f'  批量(8条):       {batch_time*1000:.1f}ms')
    print(f'  最大 RSS:        {max_rss:.1f} MB')
    print(f'  最大 USS:        {max_uss:.1f} MB')
    print(f'  模型加载增量:    {delta:.1f} MB (RSS)')


if __name__ == '__main__':
    main()
