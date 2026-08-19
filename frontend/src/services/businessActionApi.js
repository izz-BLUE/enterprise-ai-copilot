import { authenticatedFetch, AuthExpiredError } from './authApi'

const JAVA_BASE_URL = ''

export class BusinessActionApiError extends Error {
  constructor({ message, errorCode, status, traceId, httpStatus }) {
    super(message)
    this.name = 'BusinessActionApiError'
    this.errorCode = errorCode || null
    this.status = status || null
    this.traceId = traceId || null
    this.httpStatus = httpStatus ?? null
  }
}

async function postDecision({
  path,
  confirmationNonce,
  adminToken,
  idempotencyKey,
  accessToken,
}) {
  const headers = { 'Content-Type': 'application/json', Accept: 'application/json' }
  if (adminToken?.trim()) {
    headers['X-Admin-Token'] = adminToken.trim()
  }
  if (idempotencyKey) {
    headers['Idempotency-Key'] = idempotencyKey
  }
  let response
  try {
    response = await authenticatedFetch(`${JAVA_BASE_URL}${path}`, accessToken, {
      method: 'POST',
      headers,
      body: JSON.stringify({ confirmationNonce }),
    })
  } catch (error) {
    if (error instanceof AuthExpiredError) throw error
    throw new BusinessActionApiError({
      message: '网络连接失败，服务端结果未知，请使用原操作重试。',
      errorCode: 'NETWORK_ERROR',
    })
  }

  let data
  try {
    data = await response.json()
  } catch {
    throw new BusinessActionApiError({
      message: response.ok
        ? '服务响应格式异常，请稍后重试。'
        : '服务暂时不可用，请稍后重试。',
      errorCode: 'INVALID_RESPONSE',
      httpStatus: response.status,
    })
  }

  if (!response.ok) {
    throw new BusinessActionApiError({
      message: typeof data?.message === 'string' && data.message.trim()
        ? data.message
        : '业务操作未完成，请稍后重试。',
      errorCode: data?.errorCode,
      status: data?.status,
      traceId: data?.traceId,
      httpStatus: response.status,
    })
  }

  return data
}

export function confirmBusinessAction({
  actionId,
  confirmationNonce,
  idempotencyKey,
  adminToken,
  accessToken,
}) {
  return postDecision({
    path: `/api/agent/actions/${encodeURIComponent(actionId)}/confirm`,
    confirmationNonce,
    idempotencyKey,
    adminToken,
    accessToken,
  })
}

export function cancelBusinessAction({
  actionId,
  confirmationNonce,
  adminToken,
  accessToken,
}) {
  return postDecision({
    path: `/api/agent/actions/${encodeURIComponent(actionId)}/cancel`,
    confirmationNonce,
    adminToken,
    accessToken,
  })
}
