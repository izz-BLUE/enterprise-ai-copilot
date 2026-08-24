import { useCallback, useEffect, useState } from 'react'
import App from '../App.jsx'
import LoginPage from './LoginPage.jsx'
import {
  AuthExpiredError,
  clearAuthState,
  fetchCurrentUser,
  logout,
  readAuthState,
  saveAuthState,
} from '../services/authApi.js'
import { resolveUserIdentity } from '../services/chatHistoryStorage.js'

export default function AuthGate() {
  const [storedAuth] = useState(() => readAuthState())
  const [authState, setAuthState] = useState(() => {
    return storedAuth
      ? { status: 'loading', value: storedAuth }
      : { status: 'anonymous', value: null }
  })
  const [verificationAttempt, setVerificationAttempt] = useState(0)

  useEffect(() => {
    const stored = storedAuth
    if (!stored) return undefined

    const controller = new AbortController()
    fetchCurrentUser(null, controller.signal)
      .then(user => setAuthState({
        status: 'authenticated',
        value: { ...stored, user },
      }))
      .catch(error => {
        if (error.name === 'AbortError') return
        if (error instanceof AuthExpiredError) {
          clearAuthState()
          setAuthState({ status: 'anonymous', value: null })
          return
        }
        setAuthState({
          status: 'unavailable',
          value: stored,
          message: error?.message || '暂时无法验证登录状态，请检查网络后重试。',
        })
      })
    return () => controller.abort()
  }, [storedAuth, verificationAttempt])

  const handleLogin = useCallback(response => {
    setAuthState({ status: 'authenticated', value: saveAuthState(response) })
  }, [])

  const handleLogout = useCallback(async () => {
    try {
      await logout()
    } finally {
      setAuthState({ status: 'anonymous', value: null })
    }
  }, [])

  if (authState.status === 'loading') {
    return <main className="auth-loading" aria-label="正在验证登录状态">正在验证登录状态…</main>
  }
  if (authState.status === 'unavailable') {
    return (
      <main className="auth-recovery" aria-labelledby="auth-recovery-title">
        <section className="login-card">
          <h1 id="auth-recovery-title">暂时无法连接服务</h1>
          <p className="login-subtitle">{authState.message}</p>
          <button
            type="button"
            className="login-submit"
            onClick={() => {
              setAuthState({ status: 'loading', value: storedAuth })
              setVerificationAttempt(value => value + 1)
            }}
          >
            重新验证
          </button>
          <button type="button" className="auth-switch-account" onClick={handleLogout}>
            切换账号
          </button>
        </section>
      </main>
    )
  }
  if (authState.status === 'anonymous') return <LoginPage onLogin={handleLogin} />
  // <App /> 重挂载 key 与 localStorage key 必须来自同一稳定身份解析函数，
  // 避免在 userId 缺失 / fallback 到 employeeId 或 username 时出现 key 不一致，
  // 导致 A / B 账号之间的内存状态被复用。
  const { reactKey } = resolveUserIdentity(authState.value?.user)
  return (
    <App
      key={reactKey}
      authState={authState.value}
      onLogout={handleLogout}
    />
  )
}
