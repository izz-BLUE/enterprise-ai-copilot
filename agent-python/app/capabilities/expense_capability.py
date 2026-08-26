"""expense_capability.py —— P2-A Expense Workflow V1 Memory capability 声明

V2 §二十六：
- task_type = 'EXPENSE_REQUEST'
- eligible_tools = frozenset({'expense_proposal_tool'})
- 其它 expense 只读/查询 Tool（travel_record_tool / invoice_verify_tool /
  expense_status_tool）以及 rag_answer_tool **不属于** expense Memory
  eligible tool（V2 §十六 / §二十七）。

Capability Registry 是业务 eligibility 的唯一真理来源：
- 本声明由应用 bootstrap 显式加入 runtime registry；
- **不**在 DEFAULT_TOOL_TO_TASK_TYPE 手动为 expense 新增业务映射（避免双重注册）；
- 不修改 Memory Core（memory_trigger_policy / memory_write_policy /
  memory_extractor / memory_pipeline / memory_write_dispatcher）。
"""

from __future__ import annotations

from app.capabilities.memory_capability import MemoryCapability

EXPENSE_MEMORY_CAPABILITY = MemoryCapability(
    task_type='EXPENSE_REQUEST',
    eligible_tools=frozenset({'expense_proposal_tool'}),
    description=(
        'P2-A 报销业务 capability；eligible tool = expense_proposal_tool。'
        'travel_record_tool / invoice_verify_tool / expense_status_tool / '
        'rag_answer_tool 不属于本 capability（单独成功不触发 Memory Extractor）。'
    ),
)

__all__ = ['EXPENSE_MEMORY_CAPABILITY']
