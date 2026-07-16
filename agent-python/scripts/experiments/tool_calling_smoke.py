#!/usr/bin/env python3
import logging
import sys
from datetime import date
from pathlib import Path

for logger_name in ("httpx", "httpcore", "openai", "openai._base_client"):
    logging.getLogger(logger_name).setLevel(logging.WARNING)

PROJECT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_DIR))

from app.core.config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
from app.services.tool_calling_service import plan_annual_leave_action

SCENARIOS = [
    ("申请 2026-07-20 一天年假，原因为私事", "proposal"),
    ("申请一天年假，原因为私事", "clarification"),
    ("申请 2026-07-20 一天年假", "clarification"),
]


def main() -> int:
    configured = bool(DEEPSEEK_API_KEY and DEEPSEEK_BASE_URL and DEEPSEEK_MODEL)
    print(f"configuration_present={'YES' if configured else 'NO'}")
    if not configured:
        return 1
    passed = 0
    for index, (question, expected_kind) in enumerate(SCENARIOS, 1):
        result = plan_annual_leave_action(question, business_date=date(2026, 7, 16))
        ok = result.kind == expected_kind
        tool_name = getattr(result, "tool_name", "NONE")
        print(
            f"test={index} http_success={'YES' if result.kind != 'invalid' else 'NO'} "
            f"tool={tool_name} kind={result.kind} result={'PASS' if ok else 'FAIL'}"
        )
        passed += int(ok)
    print(f"summary={passed}/3")
    return 0 if passed == 3 else 1


if __name__ == "__main__":
    raise SystemExit(main())
