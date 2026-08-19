export const AUTH_STORAGE_KEY = 'enterprise-ai-copilot.auth'

export class AuthExpiredError extends Error {
  constructor() {
    super('登录状态已失效，请重新登录。')
    this.name = 'AuthExpiredError'
    this.httpStatus = 401
  }
}

export async function login(username, password) {
  let response
  try {
    response = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
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
  const response = await fetch('/api/auth/me', {
    headers: {
      Authorization: `Bearer ${accessToken}`,
      Accept: 'application/json',
    },
    cache: 'no-store',
    signal,
  })
  const data = await parseJson(response)
  if (!response.ok) {
    throw new AuthExpiredError()
  }
  return data
}

export async function authenticatedFetch(path, accessToken, options = {}) {
  if (!accessToken) {
    throw new AuthExpiredError()
  }
  const headers = new Headers(options.headers || {})
  headers.set('Authorization', `Bearer ${accessToken}`)
  const response = await fetch(path, { ...options, headers })
  if (response.status === 401) {
    throw new AuthExpiredError()
  }
  return response
}

export function readAuthState() {
  try {
    const raw = localStorage.getItem(AUTH_STORAGE_KEY)
    if (!raw) return null
    const state = JSON.parse(raw)
    if (!state?.accessToken || typeof state.accessToken !== 'string') return null
    const normalized = { accessToken: state.accessToken }
    if (raw !== JSON.stringify(normalized)) {
      localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(normalized))
    }
    return normalized
  } catch {
    return null
  }
}

export function saveAuthState(response) {
  const state = {
    accessToken: response.accessToken,
  }
  localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(state))
  return { ...state, user: response.user }
}

export function clearAuthState() {
  localStorage.removeItem(AUTH_STORAGE_KEY)
}

async function parseJson(response) {
  try {
    return await response.json()
  } catch {
    return null
  }
}
