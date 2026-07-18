#!/usr/bin/env python3
import logging
import json
import sys
from datetime import date
from pathlib import Path

for logger_name in ("httpx", "httpcore", "openai", "openai._base_client"):
    logging.getLogger(logger_name).setLevel(logging.WARNING)

PROJECT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_DIR))

from app.core.config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
from app.services.tool_calling_service import (
    FORCED_ANNUAL_LEAVE_TOOL_CHOICE,
    SYSTEM_MESSAGE,
    USER_MESSAGE,
    _get_client,
    plan_annual_leave_action,
)

SCENARIOS = [
    ("申请 2026-07-20 一天年假，原因为私事", "proposal", 1),
    ("申请一天年假，原因为私事", "clarification", 0),
    ("申请 2026-07-20 一天年假", "clarification", 0),
]


def _contract_is_valid(request: dict, question: str) -> bool:
    tools = request.get("tools", [])
    if len(tools) != 1:
        return False
    function = tools[0].get("function", {})
    serialized = json.dumps(request, ensure_ascii=False)
    return all(
        (
            request.get("messages")
            == [
                {"role": "system", "content": SYSTEM_MESSAGE},
                {"role": "user", "content": USER_MESSAGE},
            ],
            request.get("tool_choice") == FORCED_ANNUAL_LEAVE_TOOL_CHOICE,
            request.get("extra_body") == {"thinking": {"type": "disabled"}},
            request.get("max_tokens") == 64,
            set(function) == {"name", "description"},
            question not in serialized,
            "2026-07-20" not in serialized,
            "私事" not in serialized,
            "half_day" not in serialized,
            not any(
                key in request
                for key in (
                    "temperature",
                    "response_format",
                    "reasoning_effort",
                    "stream",
                    "strict",
                )
            ),
        )
    )


def main() -> int:
    configured = bool(DEEPSEEK_API_KEY and DEEPSEEK_BASE_URL and DEEPSEEK_MODEL)
    print(f"configuration_present={'YES' if configured else 'NO'}")
    if not configured:
        return 1
    real_create = _get_client().chat.completions.create
    passed = 0
    for index, (question, expected_kind, expected_calls) in enumerate(SCENARIOS, 1):
        captured = []

        def counted_create(**kwargs):
            captured.append(kwargs)
            return real_create(**kwargs)

        result = plan_annual_leave_action(
            question,
            business_date=date(2026, 7, 16),
            completion_create=counted_create,
        )
        contract_valid = (
            len(captured) == expected_calls
            and (not captured or _contract_is_valid(captured[0], question))
        )
        ok = result.kind == expected_kind and contract_valid
        tool_name = getattr(result, "tool_name", "NONE")
        print(
            f"test={index} http_success={'YES' if result.kind != 'invalid' else 'NO'} "
            f"tool={tool_name} kind={result.kind} provider_calls={len(captured)} "
            f"contract_valid={'YES' if contract_valid else 'NO'} "
            f"result={'PASS' if ok else 'FAIL'}"
        )
        passed += int(ok)
    print(f"summary={passed}/3")
    return 0 if passed == 3 else 1


if __name__ == "__main__":
    raise SystemExit(main())
