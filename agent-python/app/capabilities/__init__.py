"""app.capabilities —— Memory Capability Registration Boundary（P1-B）

本包承载"Workflow Capability Registration"边界：

  Business Module  →  MemoryCapability  →  MemoryCapabilityRegistry  →  MemoryTaskTypePolicy

设计纪律：

  * 本包 **不 import** 任何业务 / Tool / Agent / LangGraph / Database / HTTP 模块；
  * 仅承载纯 Python 数据结构（Pydantic frozen BaseModel）；
  * Memory Core（app/memory/*）仅通过 ``MemoryCapabilityRegistry`` 间接消费本包，
    不直接依赖业务模块。

导出：

  - MemoryCapability         —— 单个业务 Workflow 的 Memory 接入元数据；
  - MemoryCapabilityRegistry —— 不可变注册表，提供 task_types / eligible_tools /
                                tool_mapping / describe。
"""

from app.capabilities.memory_capability import MemoryCapability
from app.capabilities.memory_capability_registry import MemoryCapabilityRegistry


__all__ = ['MemoryCapability', 'MemoryCapabilityRegistry']