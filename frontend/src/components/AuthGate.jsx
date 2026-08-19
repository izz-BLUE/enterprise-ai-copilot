import { useCallback, useEffect, useState } from 'react'
import App from '../App.jsx'
import LoginPage from './LoginPage.jsx'
import {
  clearAuthState,
  fetchCurrentUser,
  readAuthState,
  saveAuthState,
} from '../services/authApi.js'

export default function AuthGate() {
  const [storedAuth] = useState(() => readAuthState())
  const [authState, setAuthState] = useState(() => {
    return storedAuth
      ? { status: 'loading', value: storedAuth }
      : { status: 'anonymous', value: null }
  })

  useEffect(() => {
    const stored = storedAuth
    if (!stored) return undefined

    const controller = new AbortController()
    fetchCurrentUser(stored.accessToken, controller.signal)
      .then(user => setAuthState({
        status: 'authenticated',
        value: { ...stored, user },
      }))
      .catch(error => {
        if (error.name === 'AbortError') return
        clearAuthState()
        setAuthState({ status: 'anonymous', value: null })
      })
    return () => controller.abort()
  }, [storedAuth])

  const handleLogin = useCallback(response => {
    setAuthState({ status: 'authenticated', value: saveAuthState(response) })
  }, [])

  const handleLogout = useCallback(() => {
    clearAuthState()
    setAuthState({ status: 'anonymous', value: null })
  }, [])

  if (authState.status === 'loading') {
    return <main className="auth-loading" aria-label="正在验证登录状态">正在验证登录状态…</main>
  }
  if (authState.status === 'anonymous') return <LoginPage onLogin={handleLogin} />
  return <App authState={authState.value} onLogout={handleLogout} />
}
