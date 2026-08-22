"""memory_write_dispatcher.py —— Memory Write Path 写入边界层（Phase 3F）

职责：
  将 MemoryWriteCommand 路由到具体的 Writer 客户端（Java HTTP / 持久化 /
  测试桩）。Dispatcher 是"Command → 实际写入"之间唯一合法的边界层。

边界与不变式：
  1. 只做"分发"，不做"加工"：
     - 不修改 command 字段；
     - 不注入 employee_id / user_id / conversation_id（这些属于上游调用方）；
     - 不做 retry / fallback / serialization / HTTP 协议处理。
  2. Writer 通过依赖注入（DI）支持两种契约：
     - 函数：`writer(command)`；
     - 对象：`writer.write(command)`。
     Dispatcher 启动时识别形态，后续每次调用复用。
  3. Writer 抛出的任何异常统一包装为 MemoryWriteDispatcherError，
     保留原始异常链（__cause__）。
  4. 输入类型强校验：非 MemoryWriteCommand 一律 TypeError；
     None / dict / MemoryProposal 等均不接受。

非职责（明确不做）：
  - 不调用任何 Java / HTTP / DB；
  - 不修改 LangGraph / AgentState / PlannerDecision / main.py；
  - 不修改 MemoryPipeline / MemoryWritePolicy；
  - 不新增 endpoint；
  - 不实现 writer 协议的具体实现（属于后续 Java Client / Persistence Layer）。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Union

from app.memory.memory_write_policy import MemoryWriteCommand


logger = logging.getLogger(__name__)


# Writer 协议：函数或带 .write() 方法的对象。
WriterContract = Union[
    Callable[[MemoryWriteCommand], Any],
    object,  # 实际为带有 .write(command) 方法的对象
]


class MemoryWriteDispatcherError(RuntimeError):
    """Dispatcher 调度失败：writer 抛出异常时统一包装为此错误。

    __cause__ 保留原始异常，便于上游追溯。
    """


class MemoryWriteDispatcher:
    """Memory Write Command → Writer 的边界层。

    构造时一次性识别 writer 形态（function / object），每次 dispatch
    复用同一调用方式。Writer 异常包装为 MemoryWriteDispatcherError 上抛，
    调用方负责 fail-safe 处理。
    """

    def __init__(self, writer: WriterContract | None = None) -> None:
        self._writer = writer
        self._call: Callable[[MemoryWriteCommand], Any] | None
        if writer is None:
            self._call = None
        elif callable(writer):
            # 函数 / lambda / 实现了 __call__ 的对象
            self._call = writer
        elif hasattr(writer, 'write') and callable(getattr(writer, 'write', None)):
            # 带 .write(command) 方法的对象
            self._call = writer.write  # type: ignore[assignment]
        else:
            raise TypeError(
                'MemoryWriteDispatcher.writer 必须是 callable 或带 .write() '
                f'方法的对象，得到 {type(writer).__name__}'
            )

    @property
    def writer(self) -> WriterContract | None:
        """暴露注入的 writer（用于测试与诊断）。"""
        return self._writer

    def dispatch(self, command: MemoryWriteCommand) -> Any:
        """将 MemoryWriteCommand 路由到 writer，原样返回 writer 返回值。

        抛出：
          MemoryWriteDispatcherError —— writer 抛任何异常时被包装。
          TypeError —— command 不是 MemoryWriteCommand。
        """
        if not isinstance(command, MemoryWriteCommand):
            raise TypeError(
                'MemoryWriteDispatcher.dispatch 需要 MemoryWriteCommand 输入，'
                f'得到 {type(command).__name__}'
            )

        if self._call is None:
            # 测试 / AUDIT_ONLY 兼容路径：未注入 writer 不产生 HTTP 副作用。
            # ENABLED 的 main.py 会显式注入 JavaMemoryClient；配置缺失时注入 fail-closed writer。
            logger.info(
                'MemoryWriteDispatcher: 未注入 writer，跳过 dispatch（command=%s）',
                command.action,
            )
            return None

        try:
            result = self._call(command)
        except MemoryWriteDispatcherError:
            # 已是 Dispatcher 错误，避免二次包装
            raise
        except Exception as exc:
            logger.warning(
                'MemoryWriteDispatcher: writer 异常 (type=%s): %s',
                type(exc).__name__, exc,
            )
            raise MemoryWriteDispatcherError(
                f'MemoryWriteDispatcher dispatch 失败: '
                f'{type(exc).__name__}: {exc}'
            ) from exc

        return result

    def __call__(self, command: MemoryWriteCommand) -> Any:
        """便捷调用：dispatcher(command) 等价于 dispatcher.dispatch(command)。"""
        return self.dispatch(command)
