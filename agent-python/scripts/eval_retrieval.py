#!/usr/bin/env python3
"""
eval_retrieval.py — RAG 检索评估脚本（Source + Keyword 双层评估）

评估 hybrid_retriever 的 TopK 是否命中预期知识来源，
以及预期关键词是否出现在 TopK chunk content 中。
不调用 LLM，不消耗 token。

用法:
    python agent-python/scripts/eval_retrieval.py

依赖:
    - data/eval/rag_eval_cases.json（测试集）
    - data/processed/faiss.index（需先运行 build_faiss_index.py）
"""

import json
import os
import sys

# ── 路径自动识别 ────────────────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, '..', '..'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'agent-python'))

EVAL_FILE = os.path.join(PROJECT_ROOT, 'data', 'eval', 'rag_eval_cases.json')
FAISS_INDEX = os.path.join(PROJECT_ROOT, 'data', 'processed', 'faiss.index')

TOP_K = 3


def _check_prerequisites() -> bool:
    """检查测试集和 Faiss 索引导出的前置条件。"""
    ok = True

    if not os.path.isfile(EVAL_FILE):
        print(f'评估测试集不存在: {EVAL_FILE}')
        print('   请先创建 data/eval/rag_eval_cases.json')
        ok = False

    if not os.path.isfile(FAISS_INDEX):
        print(f'Faiss 索引不存在: {FAISS_INDEX}')
        print('   请先运行 python agent-python/scripts/build_faiss_index.py')
        ok = False

    return ok


def _check_keywords(content: str, expected_keywords: list[str]) -> tuple[bool, list[str]]:
    """检查所有 expected_keywords 是否出现在 content 中。

    返回 (全部命中?, [缺失的关键词列表])
    """
    missing = []
    for kw in expected_keywords:
        if kw not in content:
            missing.append(kw)
    return len(missing) == 0, missing


def main():
    # ── 前置检查 ──
    if not _check_prerequisites():
        sys.exit(1)

    # ── 加载测试集 ──
    with open(EVAL_FILE, 'r', encoding='utf-8') as f:
        cases = json.load(f)

    print(f'加载 {len(cases)} 个测试用例\n')

    # ── 导入检索器（延迟导入，避免前置检查失败时因缺少依赖而崩溃） ──
    from app.retrieval.hybrid_retriever import retrieve

    # ── 表头 ──
    HEADER_FMT = '  {:<5}  {:>6}  {:>5}  {:>5}  {:40}  {}'
    ROW_FMT = '  {:<5}  {:>6}  {:>5}  {:>5}  {:40}  {}'
    print(HEADER_FMT.format('ID', '结果', 'SRC', 'KW', '问题', '预期来源'))
    print('  ' + '-' * 120)

    results = []
    for case in cases:
        case_id = case['id']
        question = case['question']
        expected_sources: list[str] = case['expected_sources']
        expected_keywords: list[str] = case.get('expected_keywords', [])

        # 调用 hybrid retriever
        topk = retrieve(question, top_k=TOP_K)

        # 提取实际 source_file（去重）
        actual_sources = sorted({r['source_file'] for r in topk})
        top_chunk_ids = [r['id'] for r in topk]

        # ── source_hit：预期来源是否出现在 TopK 中 ──
        source_hit = any(es in actual_sources for es in expected_sources)

        # ── keyword_hit：预期关键词是否出现在 TopK content 中 ──
        all_content = '\n'.join(r['content'] for r in topk)
        keyword_hit = True
        missing_keywords: list[str] = []
        if expected_keywords:
            keyword_hit, missing_keywords = _check_keywords(all_content, expected_keywords)

        # ── 最终判定 ──
        # 如果有 expected_keywords，必须 source_hit AND keyword_hit
        # 如果没有 expected_keywords，只按 source_hit 判断
        if expected_keywords:
            passed = source_hit and keyword_hit
        else:
            passed = source_hit

        results.append({
            'id': case_id,
            'question': question,
            'passed': passed,
            'source_hit': source_hit,
            'keyword_hit': keyword_hit,
            'missing_keywords': missing_keywords,
            'expected_sources': expected_sources,
            'expected_keywords': expected_keywords,
            'actual_sources': actual_sources,
            'top_chunk_ids': top_chunk_ids,
        })

        status = 'PASS' if passed else 'FAIL'
        src_tag = '+SRC' if source_hit else '-SRC'
        kw_tag = '+KW ' if keyword_hit else '-KW '
        display_question = question if len(question) <= 38 else question[:35] + '...'
        print(ROW_FMT.format(case_id, status, src_tag, kw_tag,
                             display_question, ', '.join(expected_sources)))

    # ── 汇总 ──
    total = len(results)
    source_hit_count = sum(1 for r in results if r['source_hit'])
    keyword_hit_count = sum(1 for r in results if r['keyword_hit'])
    passed_count = sum(1 for r in results if r['passed'])
    failed_count = total - passed_count
    source_hit_rate = (source_hit_count / total * 100) if total > 0 else 0.0
    keyword_hit_rate = (keyword_hit_count / total * 100) if total > 0 else 0.0
    final_pass_rate = (passed_count / total * 100) if total > 0 else 0.0

    print()
    print('=' * 60)
    print('  评估结果汇总')
    print('=' * 60)
    print(f'    总用例数:          {total}')
    print(f'    通过:              {passed_count}')
    print(f'    失败:              {failed_count}')
    print(f'    source_hit_rate:   {source_hit_rate:.1f}%')
    print(f'    keyword_hit_rate:  {keyword_hit_rate:.1f}%')
    print(f'    final_pass_rate:   {final_pass_rate:.1f}%')

    # ── 失败用例详情 ──
    if failed_count > 0:
        print()
        print('=' * 60)
        print('  失败用例分析')
        print('=' * 60)
        for r in results:
            if r['passed']:
                continue
            print(f'\n  ID:               {r["id"]}')
            print(f'  问题:             {r["question"]}')
            print(f'  source_hit:       {r["source_hit"]}')
            print(f'  keyword_hit:      {r["keyword_hit"]}')
            if r['missing_keywords']:
                print(f'  缺失关键词:       {r["missing_keywords"]}')
            print(f'  预期来源:         {r["expected_sources"]}')
            print(f'  实际来源:         {r["actual_sources"]}')
            print(f'  TopK chunk IDs:   {r["top_chunk_ids"]}')

    # ── 退出码 ──
    sys.exit(0 if failed_count == 0 else 1)


if __name__ == '__main__':
    main()
