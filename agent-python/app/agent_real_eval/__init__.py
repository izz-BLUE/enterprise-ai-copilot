"""agent_real_eval —— 真实 Planner 质量评估层 P0

与确定性 agent_eval 不同，本模块：
- Planner 必须调用真实 DeepSeek LLM（call_llm 不被 Patch）
- Tool 行为由确定性 Stub 提供（RAG / Eval 网络 / Embedding / Eval
  产物都不进入真实链路）
- 评估的是"真实规划器在稳定 Tool 行为下的质量"，不是最终答案对错

modules:
    cases      —— RealAgentEvalCase 定义 + 24 个固定 Case
    tool_stubs —— RAG / Eval Tool 的 Stub 实现（支持 normal/error/
                 timeout/observation_injection 四种 scenario）
    runner     —— 单条 Case 多次 Run + Scorer + Report 聚合

CLI 入口见 scripts/eval/run_agent_real_eval.py
"""

from app.agent_real_eval.cases import (
    REAL_AGENT_EVAL_CASES,
    RealAgentEvalCase,
    ToolScenario,
)
from app.agent_real_eval.runner import (
    RealEvalRunResult,
    compute_metrics,
    run_case_repeatedly,
    run_real_eval,
)
from app.agent_real_eval.tool_stubs import RealEvalToolStubs

__all__ = [
    'REAL_AGENT_EVAL_CASES',
    'RealAgentEvalCase',
    'ToolScenario',
    'RealEvalRunResult',
    'compute_metrics',
    'run_case_repeatedly',
    'run_real_eval',
    'RealEvalToolStubs',
]
