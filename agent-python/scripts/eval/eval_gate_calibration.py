#!/usr/bin/env python3
"""独立验证集的 Calibration、一次性 Holdout 与 partial Gate 评估。"""

import argparse
import json
import math
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..', '..', '..'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'agent-python'))

DATA_FILE = os.path.join(PROJECT_ROOT, 'data', 'eval', 'rag_gate_validation_candidates.json')
SPLIT_FILE = os.path.join(PROJECT_ROOT, 'data', 'eval', 'rag_gate_validation_split.json')
REPORT_DIR = os.path.join(PROJECT_ROOT, 'data', 'eval', 'reports')
CALIBRATION_REPORT = os.path.join(REPORT_DIR, 'gate_calibration_report.json')
HOLDOUT_REPORT = os.path.join(REPORT_DIR, 'gate_holdout_report.json')
PARTIAL_REPORT = os.path.join(REPORT_DIR, 'gate_partial_report.json')

VECTOR_THRESHOLDS = [0.58, 0.59, 0.60, 0.61, 0.62, 0.63, 0.65]
VECTOR_STRONG_VALUES = [0.59, 0.60, 0.61, 0.62, 0.63]
VECTOR_WEAK_VALUES = [0.54, 0.56, 0.58, 0.60]
BM25_WEAK_VALUES = [1.5, 2.0, 2.5, 3.0]


def _load_inputs() -> tuple[dict, dict, dict[str, dict]]:
    with open(DATA_FILE, encoding='utf-8') as file:
        data = json.load(file)
    with open(SPLIT_FILE, encoding='utf-8') as file:
        split = json.load(file)
    cases = {}
    for label_key, label in [('answerable', 'answerable'), ('no_answer', 'no-answer')]:
        for case in data[label_key]:
            cases[case['id']] = {**case, 'label': label}
    for case in data['needs_review']:
        cases[case['id']] = {**case, 'label': 'needs_review'}
    return data, split, cases


def _score_cases(ids: list[str], cases: dict[str, dict]) -> list[dict]:
    from app.retrieval.hybrid_retriever import retrieve_with_signals

    rows = []
    for case_id in ids:
        case = cases[case_id]
        chunks, signals = retrieve_with_signals(case['question'])
        top_chunk_id = chunks[0]['id'] if chunks else None
        top_signal = next((signal for signal in signals if signal.chunk_id == top_chunk_id), None)
        rows.append({
            'id': case_id,
            'label': case['label'],
            'category': case.get('category', ''),
            'top_candidate_chunk_id': top_chunk_id,
            'top_candidate_vector_score': top_signal.vector_score if top_signal else None,
            'top_candidate_bm25_score': top_signal.bm25_score if top_signal else None,
            'top_candidate_vector_rank': top_signal.vector_rank if top_signal else None,
            'top_candidate_bm25_rank': top_signal.bm25_rank if top_signal else None,
            'candidate_signals': [asdict(signal) for signal in signals],
        })
    return rows


def _rule_margin(row: dict, rule: dict) -> float:
    candidates = row['candidate_signals']
    if not candidates:
        return -math.inf
    if rule['scheme'] == 'vector':
        return max(
            candidate['vector_score'] - rule['vector_threshold']
            for candidate in candidates
            if candidate['vector_score'] is not None
        )

    margins = []
    for candidate in candidates:
        vector_score = candidate['vector_score']
        bm25_score = candidate['bm25_score']
        strong_margin = (
            vector_score - rule['vector_strong']
            if vector_score is not None else -math.inf
        )
        combined_margin = min(
            vector_score - rule['vector_weak'] if vector_score is not None else -math.inf,
            bm25_score - rule['bm25_weak'] if bm25_score is not None else -math.inf,
        )
        margins.append(max(strong_margin, combined_margin))
    return max(margins)


def _evaluate_rule(rows: list[dict], rule: dict) -> dict:
    evaluated = []
    for row in rows:
        margin = _rule_margin(row, rule)
        passed = margin >= 0
        expected_pass = row['label'] == 'answerable'
        signed_margin = margin if expected_pass else -margin
        evaluated.append({
            'id': row['id'], 'label': row['label'], 'passed': passed,
            'rule_margin': margin, 'classification_margin': signed_margin,
        })

    answerable = [row for row in evaluated if row['label'] == 'answerable']
    no_answer = [row for row in evaluated if row['label'] == 'no-answer']
    correct_margins = [row['classification_margin'] for row in evaluated]
    return {
        **rule,
        'answerable_pass': sum(row['passed'] for row in answerable),
        'answerable_false_reject': sum(not row['passed'] for row in answerable),
        'no_answer_block': sum(not row['passed'] for row in no_answer),
        'no_answer_false_pass': sum(row['passed'] for row in no_answer),
        'minimum_classification_margin': min(correct_margins) if correct_margins else None,
        'cases': evaluated,
    }


def _candidate_rules() -> list[dict]:
    rules = [
        {'scheme': 'vector', 'vector_threshold': threshold, 'parameter_count': 1}
        for threshold in VECTOR_THRESHOLDS
    ]
    for strong in VECTOR_STRONG_VALUES:
        for weak in VECTOR_WEAK_VALUES:
            if strong < weak:
                continue
            for bm25 in BM25_WEAK_VALUES:
                rules.append({
                    'scheme': 'combined',
                    'vector_strong': strong,
                    'vector_weak': weak,
                    'bm25_weak': bm25,
                    'parameter_count': 3,
                })
    return rules


def _select_rule(comparisons: list[dict]) -> dict:
    minimum_false_reject = min(row['answerable_false_reject'] for row in comparisons)
    pool = [
        row for row in comparisons
        if row['answerable_false_reject'] == minimum_false_reject
    ]
    return max(
        pool,
        key=lambda row: (
            row['no_answer_block'],
            row['minimum_classification_margin'],
            1 if row['scheme'] == 'vector' else 0,
            -row['parameter_count'],
        ),
    )


def _write_report(path: str, report: dict) -> None:
    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as file:
        json.dump(report, file, ensure_ascii=False, indent=2, allow_nan=False)


def run_calibration(split: dict, cases: dict[str, dict]) -> int:
    ids = split['calibration']['answerable_ids'] + split['calibration']['no_answer_ids']
    rows = _score_cases(ids, cases)
    comparisons = [_evaluate_rule(rows, rule) for rule in _candidate_rules()]
    recommended = _select_rule(comparisons)
    report = {
        'phase': 'calibration',
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'split_version': split['split_version'],
        'ids': ids,
        'scored_cases': rows,
        'candidate_count': len(comparisons),
        'comparisons': comparisons,
        'recommended_rule': {key: value for key, value in recommended.items() if key != 'cases'},
    }
    _write_report(CALIBRATION_REPORT, report)
    print(json.dumps(report['recommended_rule'], ensure_ascii=False))
    print(f'Calibration 报告: {CALIBRATION_REPORT}')
    return 0


def run_holdout(split: dict, cases: dict[str, dict]) -> int:
    if os.path.exists(HOLDOUT_REPORT):
        raise RuntimeError('Holdout 报告已存在；禁止在同一 Holdout 上重复验证')
    if not os.path.exists(CALIBRATION_REPORT):
        raise RuntimeError('Calibration 报告不存在，不能运行 Holdout')
    with open(CALIBRATION_REPORT, encoding='utf-8') as file:
        calibration = json.load(file)
    if calibration['split_version'] != split['split_version']:
        raise RuntimeError('Calibration 与当前固定划分版本不一致')

    ids = split['holdout']['answerable_ids'] + split['holdout']['no_answer_ids']
    rows = _score_cases(ids, cases)
    result = _evaluate_rule(rows, calibration['recommended_rule'])
    result_cases = {row['id']: row for row in result.pop('cases')}
    for row in rows:
        row.update(result_cases[row['id']])
    report = {
        'phase': 'holdout_once',
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'split_version': split['split_version'],
        'rule': calibration['recommended_rule'],
        'summary': result,
        'cases': rows,
    }
    _write_report(HOLDOUT_REPORT, report)
    print(json.dumps(result, ensure_ascii=False))
    print(f'Holdout 报告: {HOLDOUT_REPORT}')
    passed = result['answerable_pass'] == 8 and result['no_answer_block'] >= 6
    return 0 if passed else 1


def run_partial(split: dict, cases: dict[str, dict]) -> int:
    if not os.path.exists(CALIBRATION_REPORT):
        raise RuntimeError('Calibration 报告不存在，不能评估 partial')
    with open(CALIBRATION_REPORT, encoding='utf-8') as file:
        calibration = json.load(file)
    ids = [case_id for case_id in split['excluded_needs_review_ids'] if case_id.startswith('val_r_')]
    rows = _score_cases(ids, cases)
    for row in rows:
        margin = _rule_margin(row, calibration['recommended_rule'])
        row['gate_decision'] = 'pass' if margin >= 0 else 'block'
        row['rule_margin'] = margin
    report = {
        'phase': 'partial',
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'split_version': split['split_version'],
        'rule': calibration['recommended_rule'],
        'cases': rows,
    }
    _write_report(PARTIAL_REPORT, report)
    print(json.dumps([
        {'id': row['id'], 'gate_decision': row['gate_decision'], 'rule_margin': row['rule_margin']}
        for row in rows
    ], ensure_ascii=False))
    print(f'Partial 报告: {PARTIAL_REPORT}')
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=['calibration', 'holdout', 'partial'])
    args = parser.parse_args()
    _data, split, cases = _load_inputs()
    if args.phase == 'calibration':
        return run_calibration(split, cases)
    if args.phase == 'holdout':
        return run_holdout(split, cases)
    return run_partial(split, cases)


if __name__ == '__main__':
    raise SystemExit(main())
