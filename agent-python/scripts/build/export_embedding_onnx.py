#!/usr/bin/env python3
"""
export_embedding_onnx.py — 将 BAAI/bge-small-zh-v1.5 导出为 ONNX FP32 格式

用法:
    python scripts/build/export_embedding_onnx.py
    python scripts/build/export_embedding_onnx.py --force  # 覆盖已有目录

输出:
    agent-python/models/embedding/bge-small-zh-v1.5-onnx/
        onnx/model.onnx
        1_Pooling/config.json
        tokenizer.*
        config.json
        ...

模型二进制不提交 Git（已在 .gitignore 中排除 agent-python/models/）。
"""

import argparse
import os
import sys
import time

# ── 路径自动识别 ────────────────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, '..', '..'))
DEFAULT_OUTPUT_DIR = os.path.join(PROJECT_ROOT, 'models', 'embedding', 'bge-small-zh-v1.5-onnx')

MODEL_NAME = 'BAAI/bge-small-zh-v1.5'
EXPECTED_DIM = 512


def _verify_export(output_dir: str) -> bool:
    """验证导出产物完整性。"""
    ok = True

    # 1. ONNX 文件
    onnx_file = os.path.join(output_dir, 'onnx', 'model.onnx')
    if not os.path.isfile(onnx_file):
        print(f'  FAIL: onnx/model.onnx 不存在')
        ok = False
    else:
        size_mb = os.path.getsize(onnx_file) / 1024 / 1024
        print(f'  OK: onnx/model.onnx ({size_mb:.1f} MB)')

    # 2. Tokenizer 文件
    for name in ['tokenizer.json', 'tokenizer_config.json', 'vocab.txt']:
        path = os.path.join(output_dir, name)
        if not os.path.isfile(path):
            print(f'  FAIL: {name} 不存在')
            ok = False
        else:
            print(f'  OK: {name}')

    # 3. Pooling 配置
    pooling_config = os.path.join(output_dir, '1_Pooling', 'config.json')
    if not os.path.isfile(pooling_config):
        print(f'  FAIL: 1_Pooling/config.json 不存在')
        ok = False
    else:
        print(f'  OK: 1_Pooling/config.json')

    # 4. 模型可重新加载且输出维度正确
    if ok:
        print('\n验证模型重新加载和输出维度...')
        try:
            import numpy as np
            from sentence_transformers import SentenceTransformer

            model = SentenceTransformer(
                output_dir,
                backend='onnx',
                model_kwargs={
                    'provider': 'CPUExecutionProvider',
                    'file_name': 'onnx/model.onnx',
                    'export': False,
                },
            )
            emb = model.encode('验证测试', normalize_embeddings=True)
            dim = emb.shape[0]

            if dim != EXPECTED_DIM:
                print(f'  FAIL: 期望维度 {EXPECTED_DIM}，实际 {dim}')
                ok = False
            else:
                print(f'  OK: 输出维度 {dim}')

            norm = float(np.linalg.norm(emb))
            if abs(norm - 1.0) > 0.01:
                print(f'  FAIL: 范数异常 {norm}')
                ok = False
            else:
                print(f'  OK: 范数 {norm:.6f}')

        except Exception as e:
            print(f'  FAIL: 加载失败: {e}')
            ok = False

    return ok


def main():
    parser = argparse.ArgumentParser(description='导出 Embedding 模型为 ONNX FP32')
    parser.add_argument('--output', type=str, default=DEFAULT_OUTPUT_DIR,
                        help=f'输出目录（默认 {DEFAULT_OUTPUT_DIR}）')
    parser.add_argument('--force', action='store_true',
                        help='覆盖已有目录')
    args = parser.parse_args()

    output_dir = args.output

    # 检查输出目录
    if os.path.isdir(output_dir) and not args.force:
        print(f'错误: 输出目录已存在: {output_dir}')
        print('使用 --force 覆盖')
        sys.exit(1)

    print(f'源模型: {MODEL_NAME}')
    print(f'输出目录: {output_dir}')
    print(f'后端: ONNX (CPUExecutionProvider)')
    print()

    # 1. 加载并导出
    print('步骤 1/3: 加载模型并导出 ONNX...')
    t0 = time.time()
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        print('错误: 请先安装 sentence-transformers[onnx]')
        sys.exit(1)

    model = SentenceTransformer(
        MODEL_NAME,
        backend='onnx',
        model_kwargs={
            'export': True,
            'provider': 'CPUExecutionProvider',
        },
    )
    print(f'  导出完成，耗时 {time.time() - t0:.1f}s')

    # 2. 保存
    print('\n步骤 2/3: 保存模型...')
    os.makedirs(output_dir, exist_ok=True)
    model.save_pretrained(output_dir)
    print(f'  保存完成: {output_dir}')

    # 3. 验证
    print('\n步骤 3/3: 验证导出产物...')
    ok = _verify_export(output_dir)

    print()
    if ok:
        print('导出成功！所有验证通过。')
    else:
        print('导出完成，但验证发现问题。请检查上述错误。')
        sys.exit(1)


if __name__ == '__main__':
    main()
