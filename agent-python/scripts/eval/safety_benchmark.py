"""safety_benchmark.py —— Safety Guard Lite 离线对抗 benchmark

用法（在 agent-python 目录下）：
    python scripts/eval/safety_benchmark.py

报告四层语料（tests/safety_corpus.py）的判定结果：
  MUST_BLOCK / MUST_ALLOW 为 gating 层，统计通过率；
  KNOWN_LIMITATION 与 RESEARCH_CORPUS 为报告层（无期望值）：
  KNOWN_LIMITATION 记录当前实现不承诺覆盖的能力缺口，非 gating；
  RESEARCH_CORPUS 保留历史对抗样本，离线观察。
  两报告层不参与 CI，不要求任何通过率。

本脚本不参与普通 pytest 收集（文件名非 test_*.py）。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.guards.safety_guard import check_user_query_safety  # noqa: E402
from tests.safety_corpus import (  # noqa: E402
    KNOWN_LIMITATION,
    MUST_ALLOW,
    MUST_BLOCK,
    RESEARCH_CORPUS,
)


def main() -> None:
    print("=" * 70)
    print("Safety Guard Lite 离线对抗 benchmark")
    print("=" * 70)

    total_ok = total_n = 0
    for name, cases, want in (("MUST_BLOCK", MUST_BLOCK, False),
                              ("MUST_ALLOW", MUST_ALLOW, True)):
        ok = sum(1 for q in cases if check_user_query_safety(q)["safe"] is want)
        total_ok += ok
        total_n += len(cases)
        rate = f"{ok}/{len(cases)} ({100.0 * ok / len(cases):.1f}%)"
        print(f"{name:45s} {rate}")
        for q in cases:
            safe = check_user_query_safety(q)["safe"]
            if safe is not want:
                print(f"    FAIL safe={safe}: {q}")
    print("-" * 70)
    print(f"gating 层合计: {total_ok}/{total_n} ({100.0 * total_ok / total_n:.1f}%)")

    print(f"\nKNOWN_LIMITATION（非 gating 文档语料，无期望值）: {len(KNOWN_LIMITATION)} 条")
    for q, note in KNOWN_LIMITATION:
        safe = check_user_query_safety(q)["safe"]
        print(f"    {'BLOCK' if not safe else 'ALLOW':5s} {q}")
        print(f"          注: {note}")

    print(f"\nRESEARCH_CORPUS（离线观察，无期望值）: {len(RESEARCH_CORPUS)} 条")
    for q in RESEARCH_CORPUS:
        safe = check_user_query_safety(q)["safe"]
        print(f"    {'BLOCK' if not safe else 'ALLOW':5s} {q}")


if __name__ == "__main__":
    main()
