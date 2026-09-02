import { authenticatedFetch, AuthExpiredError, RequestTimeoutError } from './authApi.js'
import { isRetryableServerError } from './httpErrorPolicy.js'

export class MockOaApprovalApiError extends Error {
  constructor({ message, errorCode, httpStatus }) {
    super(message)
    this.name = 'MockOaApprovalApiError'
    this.errorCode = errorCode || null
    this.httpStatus = httpStatus ?? null
  }
}

const PATH = '/api/admin/mock-oa/expense-approvals'

const messageForResponse = (status, data) => {
  if (data?.errorCode === 'MOCK_OA_TIMEOUT') {
    return '模拟 OA 请求超时，结果未知，请刷新列表确认状态。'
  }
  if (status === 403) return '无管理员权限，无法访问模拟 OA 审批。'
  if (status === 404) return '审批记录不存在，可能已被其他操作处理。'
  if (status === 409) return '审批状态已发生冲突，请刷新列表后重试。'
  if (status === 503) return data?.errorCode === 'MOCK_OA_DISABLED'
    ? '模拟 OA 当前未启用，请联系管理员。'
    : '模拟 OA 暂时不可用，请稍后重试。'
  if (isRetryableServerError(status)) return '模拟 OA 暂时不可用，请稍后重试。'
  return '模拟 OA 请求未完成，请稍后重试。'
}

async function request(path, accessToken, options = {}) {
  let response
  try {
    response = await authenticatedFetch(path, accessToken, {
      ...options,
      headers: {
        Accept: 'application/json',
        ...(options.headers || {}),
      },
      cache: 'no-store',
    })
  } catch (error) {
    if (error instanceof AuthExpiredError) throw error
    if (error instanceof RequestTimeoutError) {
      throw new MockOaApprovalApiError({
        message: '模拟 OA 请求超时，结果未知，请刷新列表确认状态。',
        errorCode: 'MOCK_OA_TIMEOUT',
        httpStatus: 503,
      })
    }
    throw new MockOaApprovalApiError({
      message: '网络连接失败，模拟 OA 结果未知，请刷新列表确认状态。',
      errorCode: 'NETWORK_ERROR',
    })
  }

  let data
  try {
    data = await response.json()
  } catch {
    throw new MockOaApprovalApiError({
      message: response.ok ? '模拟 OA 响应格式异常，请稍后重试。' : messageForResponse(response.status, null),
      errorCode: 'INVALID_RESPONSE',
      httpStatus: response.status,
    })
  }
  if (!response.ok) {
    throw new MockOaApprovalApiError({
      message: messageForResponse(response.status, data),
      errorCode: data?.errorCode,
      httpStatus: response.status,
    })
  }
  return data
}

export function listMockOaApprovals({ accessToken, status, signal } = {}) {
  const params = new URLSearchParams()
  if (status) params.set('status', status)
  const query = params.toString()
  return request(`${PATH}${query ? `?${query}` : ''}`, accessToken, { signal })
}

export function decideMockOaApproval({ accessToken, requestId, decision, signal }) {
  const pathDecision = decision === 'APPROVED' ? 'approve' : 'reject'
  return request(`${PATH}/${encodeURIComponent(requestId)}/${pathDecision}`, accessToken, {
    method: 'POST',
    signal,
  })
}
