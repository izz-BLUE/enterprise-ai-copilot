export const AUTH_STORAGE_KEY = 'enterprise-ai-copilot.auth'

export class AuthExpiredError extends Error {
  constructor() {
    super('登录状态已失效，请重新登录。')
    this.name = 'AuthExpiredError'
    this.httpStatus = 401
  }
}

export class AuthServiceError extends Error {
  constructor(message, httpStatus = null) {
    super(message)
    this.name = 'AuthServiceError'
    this.httpStatus = httpStatus
  }
}

export class RequestTimeoutError extends Error {
  constructor() {
    super('请求等待时间过长，请稍后重试。')
    this.name = 'RequestTimeoutError'
  }
}

export const DEFAULT_REQUEST_TIMEOUT_MS = 55_000

function requestSignal(externalSignal, timeoutMs) {
  const controller = new AbortController()
  let timedOut = false
  const relayAbort = () => controller.abort(externalSignal?.reason)
  externalSignal?.addEventListener('abort', relayAbort, { once: true })
  const timer = Number.isFinite(timeoutMs) && timeoutMs > 0
    ? setTimeout(() => {
        timedOut = true
        controller.abort()
      }, timeoutMs)
    : null
  return {
    signal: controller.signal,
    didTimeOut: () => timedOut,
    cleanup: () => {
      if (timer) clearTimeout(timer)
      externalSignal?.removeEventListener('abort', relayAbort)
    },
  }
}

export async function login(username, password) {
  let response
  try {
    response = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ username, password }),
    })
  } catch {
    throw new Error('无法连接到 Java 后端，请确认服务已启动。')
  }

  const data = await parseJson(response)
  if (!response.ok) {
    throw new Error(typeof data?.message === 'string' && data.message.trim()
      ? data.message
      : '登录失败，请检查用户名和密码。')
  }
  return data
}

export async function fetchCurrentUser(accessToken, signal) {
  const response = await authenticatedFetch('/api/auth/me', accessToken, {
    headers: { Accept: 'application/json' },
    cache: 'no-store',
    signal,
    timeoutMs: 10_000,
  })
  const data = await parseJson(response)
  if (!response.ok) {
    throw new AuthServiceError(
      typeof data?.message === 'string' && data.message.trim()
        ? data.message
        : `登录状态校验失败（HTTP ${response.status}）。`,
      response.status,
    )
  }
  return data
}

export async function authenticatedFetch(path, accessToken, options = {}) {
  const headers = new Headers(options.headers || {})
  headers.set('X-Requested-With', 'XMLHttpRequest')
  if (accessToken) headers.set('Authorization', `Bearer ${accessToken}`)
  const { timeoutMs = DEFAULT_REQUEST_TIMEOUT_MS, signal: externalSignal, ...fetchOptions } = options
  const managedSignal = requestSignal(externalSignal, timeoutMs)
  try {
    const response = await fetch(path, {
      ...fetchOptions,
      headers,
      credentials: 'same-origin',
      signal: managedSignal.signal,
    })
    if (response.status === 401 || (response.status === 403 && path === '/api/auth/me')) {
      throw new AuthExpiredError()
    }
    return response
  } catch (error) {
    if (managedSignal.didTimeOut()) throw new RequestTimeoutError()
    throw error
  } finally {
    managedSignal.cleanup()
  }
}

export function readAuthState() {
  try {
    const raw = localStorage.getItem(AUTH_STORAGE_KEY)
    if (!raw) return null
    const state = JSON.parse(raw)
    if (state?.authenticated !== true && typeof state?.accessToken !== 'string') return null
    const normalized = { authenticated: true }
    if (raw !== JSON.stringify(normalized)) {
      localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(normalized))
    }
    return normalized
  } catch {
    return null
  }
}

export function saveAuthState(response) {
  const state = { authenticated: true }
  localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(state))
  return { ...state, user: response.user }
}

export function clearAuthState() {
  localStorage.removeItem(AUTH_STORAGE_KEY)
}

export async function logout() {
  try {
    await authenticatedFetch('/api/auth/logout', null, { method: 'POST', timeoutMs: 10_000 })
  } finally {
    clearAuthState()
  }
}

async function parseJson(response) {
  try {
    return await response.json()
  } catch {
    return null
  }
}
