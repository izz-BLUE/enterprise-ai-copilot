#!/usr/bin/env python3
"""
verify_embedding_consistency.py — PyTorch / ONNX FP32 向量一致性验证

使用固定样本比较两种后端的输出，验收线：
- 维度全部为 512
- 无 NaN/Inf
- 范数接近 1
- 同文本 Torch/ONNX cosine 最小值 >= 0.999
"""

import os
import sys
import time

import numpy as np

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AGENT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, '..', '..'))
PROJECT_ROOT = os.path.abspath(os.path.join(AGENT_ROOT, '..'))
sys.path.insert(0, AGENT_ROOT)

MODEL_NAME = 'BAAI/bge-small-zh-v1.5'
ONNX_MODEL_PATH = os.path.join(AGENT_ROOT, 'models', 'embedding', 'bge-small-zh-v1.5-onnx')

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


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def load_and_encode(backend: str, model_source: str, **kwargs):
    """加载模型并编码样本。"""
    from sentence_transformers import SentenceTransformer

    t_load = time.time()
    if backend == 'onnx':
        model = SentenceTransformer(
            model_source,
            backend='onnx',
            model_kwargs={
                'provider': 'CPUExecutionProvider',
                'file_name': 'onnx/model.onnx',
                'export': False,
            },
        )
    else:
        model = SentenceTransformer(model_source)
    load_time = time.time() - t_load

    # 单条 encode
    t_single = time.time()
    _ = model.encode(SAMPLES[0], normalize_embeddings=True)
    single_time = time.time() - t_single

    # 批量 encode
    t_batch = time.time()
    embeddings = model.encode(SAMPLES, normalize_embeddings=True)
    embeddings = np.asarray(embeddings, dtype=np.float32)
    batch_time = time.time() - t_batch

    return embeddings, load_time, single_time, batch_time


def get_model_size(path: str) -> int:
    """递归计算目录大小。"""
    total = 0
    for dirpath, _, filenames in os.walk(path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            if os.path.isfile(fp):
                total += os.path.getsize(fp)
    return total


def main():
    print('=' * 70)
    print('  PyTorch / ONNX FP32 向量一致性验证')
    print('=' * 70)

    # ── PyTorch ──
    print('\n[1/2] PyTorch FP32...')
    torch_embs, torch_load, torch_single, torch_batch = load_and_encode('torch', MODEL_NAME)
    print(f'  加载耗时: {torch_load:.2f}s')
    print(f'  单条 encode: {torch_single*1000:.1f}ms')
    print(f'  批量 encode ({len(SAMPLES)} 条): {torch_batch*1000:.1f}ms')

    # ── ONNX ──
    print('\n[2/2] ONNX FP32...')
    onnx_embs, onnx_load, onnx_single, onnx_batch = load_and_encode('onnx', ONNX_MODEL_PATH)
    print(f'  加载耗时: {onnx_load:.2f}s')
    print(f'  单条 encode: {onnx_single*1000:.1f}ms')
    print(f'  批量 encode ({len(SAMPLES)} 条): {onnx_batch*1000:.1f}ms')

    # ── 模型大小 ──
    torch_size = 0  # HuggingFace cache，不单独计算
    onnx_size = get_model_size(ONNX_MODEL_PATH)
    print(f'\n模型文件大小:')
    print(f'  ONNX 目录: {onnx_size / 1024 / 1024:.1f} MB')

    # ── 一致性比较 ──
    print('\n' + '=' * 70)
    print('  一致性比较')
    print('=' * 70)

    dim = torch_embs.shape[1]
    print(f'\n维度: Torch={torch_embs.shape}, ONNX={onnx_embs.shape}')

    # 检查 NaN/Inf
    has_nan = np.isnan(torch_embs).any() or np.isnan(onnx_embs).any()
    has_inf = np.isinf(torch_embs).any() or np.isinf(onnx_embs).any()
    print(f'NaN: {"有" if has_nan else "无"}, Inf: {"有" if has_inf else "无"}')

    # 范数
    torch_norms = np.linalg.norm(torch_embs, axis=1)
    onnx_norms = np.linalg.norm(onnx_embs, axis=1)
    print(f'\n范数:')
    print(f'  Torch 最小={torch_norms.min():.6f}, 最大={torch_norms.max():.6f}')
    print(f'  ONNX  最小={onnx_norms.min():.6f}, 最大={onnx_norms.max():.6f}')

    # 逐样本 cosine similarity
    print(f'\n逐样本 Torch/ONNX cosine similarity:')
    cosines = []
    for i, text in enumerate(SAMPLES):
        cos = cosine_similarity(torch_embs[i], onnx_embs[i])
        cosines.append(cos)
        norm_t = torch_norms[i]
        norm_o = onnx_norms[i]
        print(f'  [{i+1}] cos={cos:.6f}  norm_T={norm_t:.6f}  norm_O={norm_o:.6f}  | {text}')

    cosines = np.array(cosines)
    abs_errors = np.abs(torch_embs - onnx_embs)

    print(f'\n汇总:')
    print(f'  cosine 最小值: {cosines.min():.6f}')
    print(f'  cosine 平均值: {cosines.mean():.6f}')
    print(f'  cosine 最大值: {cosines.max():.6f}')
    print(f'  最大绝对误差:  {abs_errors.max():.6f}')

    # ── 验收判定 ──
    print('\n' + '=' * 70)
    print('  验收判定')
    print('=' * 70)

    checks = [
        ('维度一致', torch_embs.shape == onnx_embs.shape and dim == 512),
        ('无 NaN', not has_nan),
        ('无 Inf', not has_inf),
        ('范数接近 1', torch_norms.min() > 0.99 and onnx_norms.min() > 0.99),
        ('cosine >= 0.999', cosines.min() >= 0.999),
    ]

    all_pass = True
    for name, ok in checks:
        status = 'PASS' if ok else 'FAIL'
        print(f'  {name}: {status}')
        if not ok:
            all_pass = False

    print(f'\n结论: {"PASS" if all_pass else "BLOCKED"}')
    return 0 if all_pass else 1


if __name__ == '__main__':
    sys.exit(main())
