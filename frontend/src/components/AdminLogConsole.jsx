import { useCallback, useEffect, useState } from 'react'
import { authenticatedFetch, AuthExpiredError } from '../services/authApi'
import './AdminLogConsole.css'

const LEVEL_OPTIONS = [
  { value: '', label: '全部级别' },
  { value: 'INFO', label: 'INFO' },
  { value: 'WARN', label: 'WARN' },
  { value: 'ERROR', label: 'ERROR' },
]

const CATEGORY_OPTIONS = [
  { value: '', label: '全部类别' },
  { value: 'REQUEST', label: 'REQUEST' },
  { value: 'AGENT', label: 'AGENT' },
  { value: 'BUSINESS_ACTION', label: 'BUSINESS_ACTION' },
  { value: 'MEMORY', label: 'MEMORY' },
  { value: 'SECURITY', label: 'SECURITY' },
  { value: 'SYSTEM', label: 'SYSTEM' },
]

const FORBIDDEN_MESSAGE = '您没有访问日志控制台的权限。'

function formatTimestamp(value) {
  if (!value) return ''
  try {
    return new Date(value).toISOString().replace('T', ' ').slice(0, 19)
  } catch {
    return String(value)
  }
}

export default function AdminLogConsole({ accessToken, onBackToChat }) {
  const [level, setLevel] = useState('')
  const [category, setCategory] = useState('')
  const [traceId, setTraceId] = useState('')
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    const params = new URLSearchParams()
    if (level) params.set('level', level)
    if (category) params.set('category', category)
    if (traceId.trim()) params.set('traceId', traceId.trim())
    params.set('limit', '100')
    const qs = params.toString()
    try {
      const response = await authenticatedFetch(
        `/api/admin/logs${qs ? `?${qs}` : ''}`,
        accessToken,
        {
          headers: { Accept: 'application/json' },
          cache: 'no-store',
        }
      )
      let data = null
      try {
        data = await response.json()
      } catch {
        data = null
      }
      if (!response.ok) {
        if (response.status === 403) {
          setError(FORBIDDEN_MESSAGE)
          setItems([])
          return
        }
        setError(data?.message || `服务返回错误（HTTP ${response.status}）`)
        setItems([])
        return
      }
      setItems(Array.isArray(data?.items) ? data.items : [])
    } catch (requestError) {
      if (requestError instanceof AuthExpiredError) {
        setError('登录状态已失效，请重新登录。')
      } else {
        setError('请求失败，请稍后重试。')
      }
      setItems([])
    } finally {
      setLoading(false)
    }
  }, [accessToken, level, category, traceId])

  useEffect(() => {
    // load 为异步请求封装，setState 均发生在 await 之后，不构成同步
    // setState 级联渲染；"手动刷新"按钮复用同一函数。规则为静态误报。
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load()
  }, [load])

  return (
    <section className="admin-log-console" aria-label="管理员日志控制台">
      <header className="admin-log-header">
        <h2 className="admin-log-title">管理员运行日志</h2>
        <div className="admin-log-actions">
          <button
            type="button"
            className="admin-log-refresh"
            onClick={load}
            disabled={loading}
            aria-label="手动刷新"
          >
            {loading ? '刷新中…' : '手动刷新'}
          </button>
          {onBackToChat && (
            <button
              type="button"
              className="admin-log-back"
              onClick={onBackToChat}
              aria-label="返回聊天"
            >
              返回聊天
            </button>
          )}
        </div>
      </header>

      <div className="admin-log-filters">
        <label className="admin-log-filter">
          <span>级别</span>
          <select
            value={level}
            onChange={e => setLevel(e.target.value)}
            aria-label="按级别筛选"
          >
            {LEVEL_OPTIONS.map(o => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </label>
        <label className="admin-log-filter">
          <span>类别</span>
          <select
            value={category}
            onChange={e => setCategory(e.target.value)}
            aria-label="按类别筛选"
          >
            {CATEGORY_OPTIONS.map(o => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </label>
        <label className="admin-log-filter">
          <span>traceId</span>
          <input
            type="text"
            value={traceId}
            onChange={e => setTraceId(e.target.value)}
            placeholder="按 traceId 精确匹配"
            aria-label="按 traceId 筛选"
          />
        </label>
      </div>

      {error && (
        <p className="admin-log-error" role="alert">{error}</p>
      )}

      {!error && items.length === 0 && !loading && (
        <p className="admin-log-empty" role="status">暂无日志</p>
      )}

      {items.length > 0 && (
        <div className="admin-log-table-wrap">
          <table className="admin-log-table">
            <thead>
              <tr>
                <th>时间</th>
                <th>级别</th>
                <th>类别</th>
                <th>事件</th>
                <th>traceId</th>
                <th>描述</th>
              </tr>
            </thead>
            <tbody>
              {items.map(item => (
                <tr key={item.id || `${item.timestamp}-${item.event}`}>
                  <td className="admin-log-cell-time">{formatTimestamp(item.timestamp)}</td>
                  <td>
                    <span className={`admin-log-level admin-log-level-${item.level}`}>
                      {item.level}
                    </span>
                  </td>
                  <td>{item.category}</td>
                  <td>{item.event}</td>
                  <td className="admin-log-cell-trace">{item.traceId || ''}</td>
                  <td>{item.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}