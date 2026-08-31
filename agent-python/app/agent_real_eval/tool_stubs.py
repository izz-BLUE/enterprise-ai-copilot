"""tool_stubs.py —— Real Eval Stub Tool（桩工具）

设计原则：
- 不访问真实 RAG / Embedding / Eval 产物 / 网络
- Tool 输出完全确定（同一 question / report_type 永远返回相同字节）
- 每次 run_real_eval 都构造全新 RealEvalToolStubs 实例，
  状态（call_count / 注入痕迹 / 失败次数）由实例持有，
  避免不同 Run 间互相污染
- 支持 4 种 scenario：
    normal            —— 全部正常
    error_once        —— 第一次 tool 执行抛 RuntimeError
    timeout_once      —— 第一次 tool 执行抛 TimeoutError（带 timeout 字样）
    observation_injection —— Tool 输出末尾追加不可信指令文本

只在 patch 上下文外使用：runner 会 monkey patch
app.agents.tool_executor_node.rag_answer_tool / eval_report_tool，
使用本类实例的 .invoke()，保留 LangChain tool 的同形态。
"""

import json
from typing import Any

from app.agent_real_eval.cases import (
    eval_payload,
    find_rag_facts,
    observation_injection_prompt,
)

# 将被 patch 替换的两个 Tool 名称（与 Planner 决策 schema 对齐）
RAG_TOOL = 'rag_answer_tool'
EVAL_TOOL = 'eval_report_tool'


class _StubTool:
    """最小 LangChain Tool 替身，只实现 .invoke()，runner 唯一入口。"""

    def __init__(self, name: str, state: dict, scenario: str):
        self.name = name
        self._state = state
        self._scenario = scenario
        self.invoke_args: list[dict] = []

    def invoke(self, args: dict | Any) -> str:
        # LangChain tool 偶有传入 BaseModel 或 dict 的情况；这里只接 dict
        if not isinstance(args, dict):
            args = dict(args) if hasattr(args, '__iter__') else {}
        self.invoke_args.append(args)
        self._state['call_count'] += 1
        self._state['last_args'] = args

        # 第一次执行：error_once / timeout_once 触发异常
        if self._state['call_count'] == 1 and self._scenario in ('error_once', 'timeout_once'):
            if self._scenario == 'timeout_once':
                # 用专用 TimeoutError 子类，确保被 Executor 分类为 tool_timeout
                raise TimeoutError('real-eval: synthetic timeout (scenario=timeout_once)')
            raise RuntimeError('real-eval: synthetic error (scenario=error_once)')

        payload = self._build_payload(args)
        text = self._maybe_inject(payload)
        self._state['observation_append_injected'] = (
            self._scenario == 'observation_injection'
        )
        return text

    def _build_payload(self, args: dict) -> str:
        if self.name == RAG_TOOL:
            question = args.get('question') or args.get('original_question') or ''
            return find_rag_facts(question)
        report_type = args.get('report_type', 'all')
        return json.dumps(eval_payload(report_type), ensure_ascii=False)

    def _maybe_inject(self, payload: str) -> str:
        """若 scenario=observation_injection，在 payload 末尾追加诱导文本。

        关键设计：诱导文本塞在 Tool 真实结果之后，仍是 Tool 的 observation
        的一部分；Planner 拿到后会作为不可信数据参考。Stub 不修改 Planner
        system prompt，让真实 Planner 自然暴露其收敛质量。
        """
        if self._scenario != 'observation_injection':
            return payload
        return payload + observation_injection_prompt()


class RealEvalToolStubs:
    """Real Eval Tool Stub 容器，对外暴露 rag / eval 两个 Tool 替身。

    用法（仅在 runner 上下文内）：
        stubs = RealEvalToolStubs(scenario='error_once')
        with patch('app.agents.tool_executor_node.rag_answer_tool', stubs.rag), \
             patch('app.agents.tool_executor_node.eval_report_tool', stubs.eval):
            state = run_langgraph_agent(...)
        # 之后可用 stubs.stat() 读取状态
    """

    def __init__(self, scenario: str = 'normal'):
        if scenario not in ('normal', 'error_once', 'timeout_once', 'observation_injection'):
            raise ValueError(f'非法 tool_scenario: {scenario!r}')
        self._state: dict[str, Any] = {
            'scenario': scenario,
            'call_count': 0,
            'last_args': None,
            'observation_append_injected': False,
        }
        self.rag = _StubTool(RAG_TOOL, self._state, scenario)
        self.eval = _StubTool(EVAL_TOOL, self._state, scenario)

    @property
    def scenario(self) -> str:
        return self._state['scenario']

    def stat(self) -> dict:
        """当前 Stub 状态快照：用于报告聚合。"""
        return {
            'scenario': self._state['scenario'],
            'call_count': self._state['call_count'],
            'observation_append_injected': self._state['observation_append_injected'],
            'rag_invoke_arg_count': len(self.rag.invoke_args),
            'eval_invoke_arg_count': len(self.eval.invoke_args),
        }

    def reset(self) -> None:
        """重置状态：同实例复用场景。"""
        self._state['call_count'] = 0
        self._state['last_args'] = None
        self._state['observation_append_injected'] = False
        self.rag.invoke_args.clear()
        self.eval.invoke_args.clear()


def make_stub(scenario: str) -> RealEvalToolStubs:
    """快捷构造：runner 内部使用。"""
    return RealEvalToolStubs(scenario=scenario)
