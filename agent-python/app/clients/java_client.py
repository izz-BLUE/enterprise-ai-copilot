"""java_client.py —— Python → Java 内部 HTTP 客户端（只读企业 Tool 使用）

约束：
- 支持 leave_balance / leave_request / expense_status / expense_recent GET；
- 不做 retry / fallback / gateway 抽象；
- 鉴权靠 JAVA_INTERNAL_TOKEN，身份通过 employee_id 入参（已由 Java 注入到 header 后转发）；
- 任何异常都向上抛，由 Tool 转成稳定 Observation，不在客户端做熔断 / 重试。
"""

from typing import Any

import httpx

from app.core.config import JAVA_BASE_URL, JAVA_INTERNAL_TOKEN, JAVA_TIMEOUT_SECONDS


class JavaClientError(Exception):
    """Java 内部接口调用失败；message 由上游 Tool 决定如何向 Planner 暴露。"""

    def __init__(self, code: str, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class JavaReadClient:
    def __init__(
        self,
        base_url: str = JAVA_BASE_URL,
        internal_token: str = JAVA_INTERNAL_TOKEN,
        timeout_seconds: int = JAVA_TIMEOUT_SECONDS,
    ) -> None:
        self._base_url = base_url.rstrip('/')
        self._internal_token = internal_token
        self._timeout = httpx.Timeout(timeout_seconds, connect=timeout_seconds)

    def get_expense_status(
        self,
        employee_id: str,
        trace_id: str,
        expense_id: str,
    ) -> dict[str, Any]:
        return self._get(
            '/api/internal/expense/status',
            employee_id=employee_id,
            trace_id=trace_id,
            params={'expenseId': expense_id},
        )

    def list_expense_recent(
        self,
        employee_id: str,
        trace_id: str,
        limit: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, str] = {}
        if limit is not None:
            params['limit'] = str(limit)
        return self._get(
            '/api/internal/expense/recent',
            employee_id=employee_id,
            trace_id=trace_id,
            params=params or None,
        )

    def get_leave_balance(
        self,
        employee_id: str,
        trace_id: str,
    ) -> dict[str, Any]:
        return self._get(
            '/api/internal/leave/balance',
            employee_id=employee_id,
            trace_id=trace_id,
        )

    def list_leave_requests(
        self,
        employee_id: str,
        trace_id: str,
        limit: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, str] = {}
        if limit is not None:
            params['limit'] = str(limit)
        return self._get(
            '/api/internal/leave/requests',
            employee_id=employee_id,
            trace_id=trace_id,
            params=params or None,
        )

    def _get(
        self,
        path: str,
        employee_id: str,
        trace_id: str,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if not self._base_url:
            raise JavaClientError('LEAVE_READ_DISABLED', 'Java 内部只读接口未启用。')
        if not self._internal_token:
            raise JavaClientError('LEAVE_READ_FORBIDDEN', '缺少内部调用凭证。')
        if not employee_id:
            raise JavaClientError('EMPLOYEE_ID_REQUIRED', '缺少员工身份。')

        headers = {
            'X-Internal-Token': self._internal_token,
            'X-Employee-Id': employee_id,
        }
        if trace_id:
            headers['X-Trace-Id'] = trace_id

        try:
            response = httpx.get(
                f'{self._base_url}{path}',
                params=params,
                headers=headers,
                timeout=self._timeout,
            )
        except httpx.TimeoutException as exc:
            raise JavaClientError('JAVA_TIMEOUT', '查询 Java 内部接口超时。') from exc
        except httpx.HTTPError as exc:
            raise JavaClientError('JAVA_UNREACHABLE', '无法访问 Java 内部接口。') from exc

        if response.status_code >= 500:
            raise JavaClientError('JAVA_INTERNAL_ERROR', 'Java 内部接口异常。',
                                  status_code=response.status_code)

        if response.status_code == 401 or response.status_code == 403:
            raise JavaClientError('LEAVE_READ_FORBIDDEN', 'Java 内部接口鉴权失败。',
                                  status_code=response.status_code)
        if response.status_code == 404:
            raise JavaClientError('LEAVE_NOT_FOUND', '未找到对应记录。',
                                  status_code=response.status_code)
        if response.status_code >= 400:
            # 业务错误码优先从 response body 读取；status 退化为兜底
            payload = self._safe_json(response)
            raise JavaClientError(
                payload.get('errorCode', 'JAVA_BAD_REQUEST')
                if isinstance(payload, dict) else 'JAVA_BAD_REQUEST',
                payload.get('message', 'Java 内部接口请求被拒绝。')
                if isinstance(payload, dict) else 'Java 内部接口请求被拒绝。',
                status_code=response.status_code,
            )

        payload = self._safe_json(response)
        if not isinstance(payload, dict):
            raise JavaClientError('JAVA_BAD_RESPONSE', 'Java 内部接口返回格式异常。',
                                  status_code=response.status_code)
        return payload

    @staticmethod
    def _safe_json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return None


_DEFAULT_CLIENT: JavaReadClient | None = None


def get_java_client() -> JavaReadClient:
    """单例客户端；测试可通过 monkeypatch 替换。"""
    global _DEFAULT_CLIENT
    if _DEFAULT_CLIENT is None:
        _DEFAULT_CLIENT = JavaReadClient()
    return _DEFAULT_CLIENT