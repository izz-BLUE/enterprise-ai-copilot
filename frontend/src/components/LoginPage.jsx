import { useState } from 'react'
import { login } from '../services/authApi'
import './LoginPage.css'

export default function LoginPage({ onLogin }) {
  const [username, setUsername] = useState('zhangsan')
  const [password, setPassword] = useState('')
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)

  const submit = async event => {
    event.preventDefault()
    if (!username.trim() || !password) return
    setLoading(true)
    setError(null)
    try {
      const response = await login(username.trim(), password)
      onLogin(response)
    } catch (requestError) {
      setError(requestError.message || '登录失败，请稍后重试。')
    } finally {
      setLoading(false)
    }
  }

  return (
    <main className="login-page">
      <section className="login-card" aria-labelledby="login-title">
        <div className="login-brand">Enterprise AI Copilot</div>
        <h1 id="login-title">登录工作台</h1>
        <p className="login-subtitle">使用企业账号访问智能体与知识库能力</p>

        <form onSubmit={submit} className="login-form">
          <label htmlFor="login-username">用户名</label>
          <input
            id="login-username"
            name="username"
            value={username}
            onChange={event => setUsername(event.target.value)}
            autoComplete="username"
            disabled={loading}
          />

          <label htmlFor="login-password">密码</label>
          <input
            id="login-password"
            name="password"
            type="password"
            value={password}
            onChange={event => setPassword(event.target.value)}
            autoComplete="current-password"
            disabled={loading}
          />

          <p className="login-demo-hint">演示账号：张三（普通员工）</p>
          {error && <p className="login-error" role="alert">{error}</p>}

          <button type="submit" className="login-submit" disabled={loading || !username.trim() || !password}>
            {loading ? '登录中…' : '登录'}
          </button>
        </form>
      </section>
    </main>
  )
}
