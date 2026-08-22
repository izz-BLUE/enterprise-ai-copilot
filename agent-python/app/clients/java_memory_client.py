"""java_memory_client.py —— Python → Java Memory Write 客户端契约（Phase 4A）

职责：
  将 MemoryWriteCommand 序列化为 HTTP 请求 payload，交给注入的 http_client
  发到 Java 侧 AiTaskMemoryService 写入 endpoint。

边界与不变式：
  1. **Identity boundary**（最重要的不变量）：
     Python 客户端不接受 user_id / employee_id / permission / allow_eval /
     allow_business_actions。conversation_id 与 Java 签发的 opaque scope
     由 Java 请求上下文注入；Python 只将它们透传到 Java，不参与 owner 判定。
  2. **Payload boundary**：
     Request body 仅允许包含 5 个字段：
       action / taskType / status / taskState / summary
     任何客户端传入的额外字段都在序列化阶段被剔除。
  3. **HTTP client 抽象**：
     仅依赖 http_client.post(url, json=payload, headers=optional) 协议，
     不绑定 httpx / requests；测试可注入 mock，无需真实网络。
  4. **不做 retry / fallback / serialization 之外的转换**：
     - 不做 JSON 序列化（交给 http_client）；
     - 不读 response body 的业务字段（Phase 4A 仅返回 http_client 返回值）；
     - 不做熔断 / 限流 / 缓存。

非职责（明确不做）：
  - 不决定 user_id；不从 command / task_state 读取身份；
  - 不写数据库 / 不读环境变量（配置由调用方注入）；
  - 不做 retry / fallback。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Protocol
from urllib.parse import quote

from app.memory.memory_write_policy import MemoryWriteCommand


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Payload 白名单 + 序列化映射
# ---------------------------------------------------------------------------

# 仅允许出现在 outbound payload 的字段（camelCase，对齐 Java 侧 DTO）。
_PAYLOAD_FIELD_MAP: dict[str, str] = {
    'action': 'action',
    'task_type': 'taskType',
    'status': 'status',
    'task_state': 'taskState',
    'summary': 'summary',
}


def _serialize_command(command: MemoryWriteCommand) -> dict[str, Any]:
    """MemoryWriteCommand → Java 侧 Request payload。

    只挑白名单字段，model_dump 的多余字段一律丢弃。
    防御 model_dump 未来扩展额外字段时的潜在泄漏。
    """
    raw = command.model_dump()
    payload: dict[str, Any] = {}
    for py_field, json_field in _PAYLOAD_FIELD_MAP.items():
        if py_field in raw:
            payload[json_field] = raw[py_field]
    return payload


# ---------------------------------------------------------------------------
# HTTP client 协议
# ---------------------------------------------------------------------------


class _HttpPostCallable(Protocol):
    """测试注入的 HTTP 客户端契约：必须支持 .post(url, json=payload, headers=optional)。

    返回值原样 passthrough 给 write_memory 调用方。
    httpx / requests / 自定义 mock 都满足此最小契约。
    """

    def post(
        self,
        url: str,
        json: dict[str, Any],  # noqa: A002
        headers: dict[str, str] | None = None,
    ) -> Any: ...


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class JavaMemoryClientError(RuntimeError):
    """Java Memory 写入失败：HTTP 客户端抛任何异常时统一包装。

    __cause__ 保留原始异常链，便于上游追溯。
    """


class JavaMemoryClient:
    """Python → Java Memory Write 客户端契约。

    构造：
      http_client —— 实现 .post(url, json=payload, headers=optional) 协议的对象（httpx / mock）；
      base_url —— Java 侧 base URL（不带 trailing slash）；
      conversation_id —— Java 已解析的会话命名空间；
      internal_token / scope_token —— Java → Python → Java 的服务间凭证与
        Java 签发 scope，均作为 header 透传，不进入 body。

    用法：
      client = JavaMemoryClient(
          http_client=httpx,
          base_url=JAVA_BASE_URL,
          conversation_id=conversation_id,
          internal_token=JAVA_INTERNAL_TOKEN,
          scope_token=java_issued_scope,
      )
      client.write_memory(command)
    """

    def __init__(
        self,
        http_client: _HttpPostCallable,
        base_url: str,
        conversation_id: str,
        internal_token: str = '',
        scope_token: str = '',
        trace_id: str = '',
    ) -> None:
        if http_client is None:
            raise ValueError('JavaMemoryClient.http_client 不能为空')
        if not hasattr(http_client, 'post') or not callable(getattr(http_client, 'post', None)):
            raise TypeError(
                'JavaMemoryClient.http_client 必须实现 .post(url, json=payload) 协议，'
                f'得到 {type(http_client).__name__}'
            )
        if not base_url or not isinstance(base_url, str):
            raise ValueError('JavaMemoryClient.base_url 必须为非空字符串')
        if not conversation_id or not isinstance(conversation_id, str):
            raise ValueError('JavaMemoryClient.conversation_id 必须为非空字符串')
        safe_conversation_id = conversation_id.strip()
        if not re.fullmatch(r'[A-Za-z0-9._\-:]{1,64}', safe_conversation_id):
            raise ValueError('JavaMemoryClient.conversation_id 格式非法')

        self._http_client = http_client
        self._base_url = base_url.rstrip('/')
        self._conversation_id = safe_conversation_id
        self._internal_token = internal_token or ''
        self._scope_token = scope_token or ''
        self._trace_id = trace_id or ''

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def http_client(self) -> _HttpPostCallable:
        """暴露注入的 http_client（用于测试 / 诊断）。"""
        return self._http_client

    @property
    def conversation_id(self) -> str:
        return self._conversation_id

    def write_memory(self, command: MemoryWriteCommand) -> Any:
        """将 MemoryWriteCommand POST 到 Java AiTaskMemoryService 写入 endpoint。

        抛出：
          TypeError —— command 不是 MemoryWriteCommand。
          JavaMemoryClientError —— http_client 抛任何异常时被包装。
        """
        if not isinstance(command, MemoryWriteCommand):
            raise TypeError(
                'JavaMemoryClient.write_memory 需要 MemoryWriteCommand 输入，'
                f'得到 {type(command).__name__}'
            )

        payload = _serialize_command(command)

        # 日志仅打印 action / taskType（不打印 task_state 中可能含敏感字段）
        logger.info(
            'JavaMemoryClient.write_memory: action=%s taskType=%s',
            payload.get('action'), payload.get('taskType'),
        )

        encoded_conversation_id = quote(self._conversation_id, safe='A-Za-z0-9._-:')
        url = (
            f'{self._base_url}/api/internal/memory/conversations/'
            f'{encoded_conversation_id}/write'
        )
        headers: dict[str, str] = {}
        if self._internal_token:
            headers['X-Internal-Token'] = self._internal_token
        if self._scope_token:
            headers['X-Memory-Write-Scope'] = self._scope_token
        if self._trace_id:
            headers['X-Trace-Id'] = self._trace_id

        try:
            if headers:
                response = self._http_client.post(url, json=payload, headers=headers)
            else:
                response = self._http_client.post(url, json=payload)
        except JavaMemoryClientError:
            # 已是 Client 错误，避免二次包装
            raise
        except Exception as exc:
            logger.warning(
                'JavaMemoryClient.write_memory: HTTP 异常 (type=%s): %s',
                type(exc).__name__, exc,
            )
            raise JavaMemoryClientError(
                f'JavaMemoryClient write_memory 失败: '
                f'{type(exc).__name__}: {exc}'
            ) from exc

        status_code = getattr(response, 'status_code', None)
        if isinstance(status_code, int) and status_code >= 400:
            raise JavaMemoryClientError(
                f'JavaMemoryClient write_memory HTTP {status_code}'
            )

        return response

    # 给 Dispatcher 友好：write_memory 也可直接作为 callable writer 注入
    def __call__(self, command: MemoryWriteCommand) -> Any:
        return self.write_memory(command)
