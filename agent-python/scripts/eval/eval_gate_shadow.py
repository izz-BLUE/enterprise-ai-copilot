#!/usr/bin/env python3
"""对现有 38 条数据执行固定关闭 Gate 的生成前门控回归评估。"""

import json
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..', '..', '..'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'agent-python'))

EVAL_FILE = os.path.join(PROJECT_ROOT, 'data', 'eval', 'rag_eval_cases.json')
REPORT_FILE = os.path.join(PROJECT_ROOT, 'data', 'eval', 'reports', 'gate_shadow_eval_report.json')


def main() -> int:
    from app.retrieval.hybrid_retriever import retrieve_with_signals
    from app.retrieval.retrieval_gate import evaluate_gate

    with open(EVAL_FILE, encoding='utf-8') as file:
        cases = json.load(file)

    if len(cases) != 38:
        raise RuntimeError(f'门控评估集应为 38 条，实际为 {len(cases)} 条')

    results = []
    for case in cases:
        chunks, signals = retrieve_with_signals(case['question'])
        decision = evaluate_gate(signals)
        results.append({
            'id': case['id'],
            'label': 'answerable' if case.get('answerable', True) else 'no-answer',
            'top_candidates': [asdict(signal) for signal in signals],
            'retrieved_chunk_ids': [chunk['id'] for chunk in chunks],
            'gate_decision': 'pass' if decision.answerable else 'block',
            'reason_code': decision.reason_code,
            'matched_chunk': decision.matched_chunk_id,
            'vector_score': decision.matched_vector_score,
            'bm25_score': decision.matched_bm25_score,
        })

    answerable = [row for row in results if row['label'] == 'answerable']
    no_answer = [row for row in results if row['label'] == 'no-answer']
    summary = {
        'answerable_pass': sum(row['gate_decision'] == 'pass' for row in answerable),
        'answerable_reject': sum(row['gate_decision'] == 'block' for row in answerable),
        'no_answer_shadow_block': sum(row['gate_decision'] == 'block' for row in no_answer),
        'no_answer_shadow_pass': sum(row['gate_decision'] == 'pass' for row in no_answer),
    }
    report = {
        'eval_type': 'gate_shadow',
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'selection_data_warning': '当前 38 条同时用于阈值选择，不代表独立验证效果。',
        'total': len(results),
        'summary': summary,
        'cases': results,
    }
    os.makedirs(os.path.dirname(REPORT_FILE), exist_ok=True)
    with open(REPORT_FILE, 'w', encoding='utf-8') as file:
        json.dump(report, file, ensure_ascii=False, indent=2)

    print(json.dumps(summary, ensure_ascii=False))
    print(f'报告已生成: {REPORT_FILE}')
    return 0 if summary == {
        'answerable_pass': 28,
        'answerable_reject': 0,
        'no_answer_shadow_block': 0,
        'no_answer_shadow_pass': 10,
    } else 1


if __name__ == '__main__':
    raise SystemExit(main())
