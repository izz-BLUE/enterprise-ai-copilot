"""LangGraph PostgreSQL 执行快照的进程级 Runtime。

本模块管理 Checkpointer 的生命周期、Graph 编译、轻量就绪探针、P3-2
execution_history 的最新快照读取、P3-3 recovery inspection 与单进程
thread guard。它不执行 Planner、Memory、业务动作或 HITL 恢复命令。
"""

import re
from datetime import date
from threading import Lock
from typing import Any

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.agents.execution_history_policy import merge_execution_history
from app.agents.langgraph_agent import compile_agent_graph, compile_agent_loop_graph
from app.core.config import (
    AI_MAX_CONCURRENT_REQUESTS,
    LANGGRAPH_CHECKPOINT_CONNECT_TIMEOUT_SECONDS,
    LANGGRAPH_CHECKPOINT_DSN,
    LANGGRAPH_CHECKPOINT_MODE,
    logger,
)
from app.runtime.execution_recovery import RecoveryDecision
from app.runtime.execution_recovery import inspect_recovery as inspect_recovery_snapshot

_RUNTIME_THREAD_ID_PATTERN = re.compile(r'rt_[0-9a-f]{64}')
_PLANNER_THREAD_SUFFIX = ':planner-v1'
_DETERMINISTIC_THREAD_SUFFIX = ':deterministic-v1'


class CheckpointRuntime:
    """服务生命周期内复用 PostgreSQL Pool、Saver 与两套已编译 Graph。"""

    def __init__(
        self,
        *,
        mode: str,
        dsn: str,
        connect_timeout_seconds: int,
        max_connections: int,
    ) -> None:
        self._mode = mode
        self._dsn = dsn
        self._connect_timeout_seconds = connect_timeout_seconds
        self._max_connections = max_connections
        self._pool: ConnectionPool | None = None
        self._saver: PostgresSaver | None = None
        self._planner_graph: Any | None = None
        self._deterministic_graph: Any | None = None
        # P3-2：单 worker 进程内只序列化同一个 LangGraph thread；不创建永久 Lock。
        self.active_thread_ids: set[str] = set()
        self._active_thread_ids_lock = Lock()

    @classmethod
    def from_config(cls) -> 'CheckpointRuntime':
        return cls(
            mode=LANGGRAPH_CHECKPOINT_MODE,
            dsn=LANGGRAPH_CHECKPOINT_DSN,
            connect_timeout_seconds=LANGGRAPH_CHECKPOINT_CONNECT_TIMEOUT_SECONDS,
            max_connections=AI_MAX_CONCURRENT_REQUESTS,
        )

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def enabled(self) -> bool:
        return self._mode == 'POSTGRES'

    def start(self) -> None:
        """初始化 POSTGRES Runtime；失败时关闭已打开资源并向上抛出安全错误。"""
        if not self.enabled:
            logger.info('LangGraph checkpoint initialized mode=DISABLED')
            return
        if not self._dsn:
            raise RuntimeError('LANGGRAPH_CHECKPOINT_DSN 未配置')

        try:
            self._pool = ConnectionPool(
                self._dsn,
                min_size=1,
                max_size=self._max_connections,
                timeout=self._connect_timeout_seconds,
                open=True,
                kwargs={
                    'autocommit': True,
                    'row_factory': dict_row,
                    'connect_timeout': self._connect_timeout_seconds,
                },
            )
            self._pool.wait(timeout=self._connect_timeout_seconds)
            serializer = JsonPlusSerializer(allowed_msgpack_modules=None)
            self._saver = PostgresSaver(self._pool, serde=serializer)
            self._saver.setup()
            self._planner_graph = compile_agent_loop_graph(checkpointer=self._saver)
            self._deterministic_graph = compile_agent_graph(checkpointer=self._saver)
        except Exception as exc:
            logger.error(
                'LangGraph checkpoint startup failed mode=POSTGRES database=postgresql error_type=%s',
                type(exc).__name__,
            )
            self.shutdown()
            raise RuntimeError('LangGraph PostgreSQL checkpoint 初始化失败') from None

        logger.info('LangGraph checkpoint initialized mode=POSTGRES database=postgresql')

    def get_graph(self, use_planner: bool) -> Any | None:
        """返回启动时编译的持久化 Graph；DISABLED 模式无需持久化 Graph。"""
        if not self.enabled:
            return None
        graph = self._planner_graph if use_planner else self._deterministic_graph
        if graph is None:
            raise RuntimeError('LangGraph checkpoint graph 尚未初始化')
        return graph

    def try_acquire_thread(self, thread_id: str) -> bool:
        """原子占用 thread；已在执行时立即返回 False，不排队。"""
        with self._active_thread_ids_lock:
            if thread_id in self.active_thread_ids:
                return False
            self.active_thread_ids.add(thread_id)
            return True

    def release_thread(self, thread_id: str) -> None:
        """释放 thread guard；discard 保证异常路径幂等且不产生泄漏。"""
        with self._active_thread_ids_lock:
            self.active_thread_ids.discard(thread_id)

    def load_execution_history(
        self,
        *,
        graph: Any,
        thread_id: str,
        memory_context: dict | None,
    ) -> list[dict]:
        """从最新 Checkpoint hydrate 当前任务允许读取的 execution_history。

        只有 Java 提供的 ACTIVE Memory 才能打开任务连续性闸门；数据库读取异常
        原样抛出，由 endpoint 返回 POSTGRES checkpoint failure（503），不静默降级。
        """
        if not self.enabled:
            return []
        if not isinstance(memory_context, dict) or memory_context.get('status') != 'ACTIVE':
            return []
        task_type = memory_context.get('taskType') or memory_context.get('task_type')
        if not isinstance(task_type, str) or not task_type:
            return []

        snapshot = graph.get_state({'configurable': {'thread_id': thread_id}})
        if snapshot is None:
            return []
        values = getattr(snapshot, 'values', None)
        if not isinstance(values, dict):
            return []

        # merge_execution_history 负责严格 Schema、稳定去重与 16 条上限；随后
        # 再按当前 ACTIVE Memory 的 task_type 做任务隔离。
        history = merge_execution_history(values.get('execution_history', []), [])
        return [entry for entry in history if entry.get('task_type') == task_type]

    def inspect_recovery(
        self,
        *,
        graph: Any,
        thread_id: str,
        question: str,
        business_date: date | None,
        employee_id: str,
        allow_eval: bool,
        allow_business_actions: bool,
    ) -> RecoveryDecision:
        """Inspect only the latest head and classify automatic crash recovery."""
        snapshot = graph.get_state({'configurable': {'thread_id': thread_id}})
        return inspect_recovery_snapshot(
            snapshot,
            question=question,
            business_date=business_date,
            employee_id=employee_id,
            allow_eval=allow_eval,
            allow_business_actions=allow_business_actions,
        )

    def build_thread_id(self, runtime_thread_id: str, use_planner: bool) -> str:
        """校验 Java 生成的基础 ID，并附加不可由客户端控制的拓扑后缀。"""
        if not _RUNTIME_THREAD_ID_PATTERN.fullmatch(runtime_thread_id):
            raise ValueError('X-Agent-Thread-Id 格式无效')
        suffix = _PLANNER_THREAD_SUFFIX if use_planner else _DETERMINISTIC_THREAD_SUFFIX
        return runtime_thread_id + suffix

    def readiness(self) -> dict[str, bool]:
        """Readiness 才探测 PostgreSQL；liveness 不受临时数据库抖动影响。"""
        if not self.enabled:
            return {'enabled': False, 'ready': True}
        if self._pool is None:
            return {'enabled': True, 'ready': False}
        try:
            with self._pool.connection() as connection:
                connection.execute('SELECT 1').fetchone()
        except Exception as exc:
            logger.warning(
                'LangGraph checkpoint readiness failed mode=POSTGRES database=postgresql error_type=%s',
                type(exc).__name__,
            )
            return {'enabled': True, 'ready': False}
        return {'enabled': True, 'ready': True}

    def shutdown(self) -> None:
        """关闭进程级 Pool；PostgresSaver 不拥有独立连接资源。"""
        pool, self._pool = self._pool, None
        self._saver = None
        self._planner_graph = None
        self._deterministic_graph = None
        with self._active_thread_ids_lock:
            self.active_thread_ids.clear()
        if pool is not None:
            try:
                pool.close()
            except Exception as exc:
                logger.warning(
                    'LangGraph checkpoint shutdown failed mode=%s error_type=%s',
                    self._mode,
                    type(exc).__name__,
                )
