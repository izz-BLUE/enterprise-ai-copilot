#!/usr/bin/env python3
"""比较 Torch 和 ONNX 的 Top3 chunk ID 一致性。"""

import json
import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AGENT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, '..', '..'))
PROJECT_ROOT = os.path.abspath(os.path.join(AGENT_ROOT, '..'))
sys.path.insert(0, AGENT_ROOT)

EVAL_FILE = os.path.join(PROJECT_ROOT, 'data', 'eval', 'rag_eval_cases.json')
REPORT_FILE = os.path.join(PROJECT_ROOT, 'data', 'eval', 'reports', 'retrieval_eval_report.json')


def run_eval(backend: str, model_path: str = ''):
    """运行 eval 并返回 case 结果。"""
    import subprocess
    env = os.environ.copy()
    if backend == 'onnx':
        env['EMBEDDING_BACKEND'] = 'onnx'
        env['EMBEDDING_MODEL_PATH'] = model_path
    else:
        env.pop('EMBEDDING_BACKEND', None)
        env.pop('EMBEDDING_MODEL_PATH', None)

    cmd = [
        sys.executable, 'scripts/eval/eval_retrieval.py',
        '--rewrite-mode', 'none',
        '--min-source-hit-rate', '0',
        '--min-keyword-hit-rate', '0',
        '--min-final-pass-rate', '0',
    ]
    subprocess.run(cmd, env=env, capture_output=True, cwd=AGENT_ROOT)

    with open(REPORT_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)['cases']


def main():
    print('运行 Torch eval...')
    torch_cases = run_eval('torch')

    print('运行 ONNX eval...')
    onnx_cases = run_eval('onnx', 'models/embedding/bge-small-zh-v1.5-onnx')

    # 比较
    torch_map = {c['id']: c for c in torch_cases}
    onnx_map = {c['id']: c for c in onnx_cases}

    all_ids = sorted(set(list(torch_map.keys()) + list(onnx_map.keys())))

    top1_match = 0
    top3_match = 0
    total = len(all_ids)
    changed = []

    for cid in all_ids:
        t_ids = torch_map.get(cid, {}).get('top_chunk_ids', [])
        o_ids = onnx_map.get(cid, {}).get('top_chunk_ids', [])

        if t_ids and o_ids:
            if t_ids[0] == o_ids[0]:
                top1_match += 1
            if set(t_ids) == set(o_ids):
                top3_match += 1
            else:
                changed.append({
                    'id': cid,
                    'torch': t_ids,
                    'onnx': o_ids,
                })

    print(f'\n总计: {total} 个 case')
    print(f'Top1 一致率: {top1_match}/{total} ({top1_match/total*100:.1f}%)')
    print(f'Top3 集合一致率: {top3_match}/{total} ({top3_match/total*100:.1f}%)')

    if changed:
        print(f'\n变化的 case ({len(changed)} 个):')
        for c in changed:
            print(f'  {c["id"]}:')
            print(f'    Torch: {c["torch"]}')
            print(f'    ONNX:  {c["onnx"]}')
    else:
        print('\n无变化 case。')

    return 0


if __name__ == '__main__':
    sys.exit(main())
