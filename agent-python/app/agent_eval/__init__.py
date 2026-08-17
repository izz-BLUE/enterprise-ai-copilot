"""agent_eval —— Agent Loop 固定回归集与评估 Runner

Agent Loop 负责"做"，Agent Eval 负责判断"做得对不对"。
确定性回归（tests/test_agent_eval.py）通过注入 mock/stub Planner 响应与
Tool 结果驱动，不依赖真实模型与网络；真实模型评估见
scripts/eval/run_agent_benchmark.py（手工运行，非 CI 门禁）。
"""
