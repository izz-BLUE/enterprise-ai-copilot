import { useCallback, useEffect, useRef, useState } from 'react'
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
const PAGE_SIZE = 50

function formatTimestamp(value) {
  if (!value) return ''
  const date = new Date(value)
  if (!Number.isFinite(date.getTime())) {
    return String(value)
  }
  return new Intl.DateTimeFormat(undefined, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(date)
}

export default function AdminLogConsole({ accessToken, onBackToChat }) {
  const [level, setLevel] = useState('')
  const [category, setCategory] = useState('')
  const [traceId, setTraceId] = useState('')
  const [debouncedTraceId, setDebouncedTraceId] = useState('')
  const [items, setItems] = useState([])
  const [offset, setOffset] = useState(0)
  const [total, setTotal] = useState(0)
  const [hasMore, setHasMore] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const activeRequestRef = useRef(null)
  const requestSequenceRef = useRef(0)

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebouncedTraceId(traceId.trim())
      setOffset(0)
    }, 300)
    return () => window.clearTimeout(timer)
  }, [traceId])

  const load = useCallback(async () => {
    activeRequestRef.current?.abort()
    const controller = new AbortController()
    activeRequestRef.current = controller
    const sequence = requestSequenceRef.current + 1
    requestSequenceRef.current = sequence
    setLoading(true)
    setError(null)
    const params = new URLSearchParams()
    if (level) params.set('level', level)
    if (category) params.set('category', category)
    if (debouncedTraceId) params.set('traceId', debouncedTraceId)
    params.set('limit', String(PAGE_SIZE))
    params.set('offset', String(offset))
    const qs = params.toString()
    try {
      const response = await authenticatedFetch(
        `/api/admin/logs${qs ? `?${qs}` : ''}`,
        accessToken,
        {
          headers: { Accept: 'application/json' },
          cache: 'no-store',
          signal: controller.signal,
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
          if (sequence === requestSequenceRef.current) {
            setError(FORBIDDEN_MESSAGE)
            setItems([])
            setTotal(0)
            setHasMore(false)
          }
          return
        }
        if (sequence === requestSequenceRef.current) {
          setError(data?.message || `服务返回错误（HTTP ${response.status}）`)
          setItems([])
          setTotal(0)
          setHasMore(false)
        }
        return
      }
      if (sequence === requestSequenceRef.current) {
        setItems(Array.isArray(data?.items) ? data.items : [])
        setTotal(Number.isInteger(data?.total) ? data.total : (data?.items?.length || 0))
        setHasMore(Boolean(data?.hasMore))
      }
    } catch (requestError) {
      if (requestError?.name === 'AbortError') return
      if (sequence !== requestSequenceRef.current) return
      if (requestError instanceof AuthExpiredError) {
        setError('登录状态已失效，请重新登录。')
      } else {
        setError('请求失败，请稍后重试。')
      }
      setItems([])
      setTotal(0)
      setHasMore(false)
    } finally {
      if (sequence === requestSequenceRef.current) {
        setLoading(false)
        activeRequestRef.current = null
      }
    }
  }, [accessToken, level, category, debouncedTraceId, offset])

  useEffect(() => {
    // load 为异步请求封装，setState 均发生在 await 之后，不构成同步
    // setState 级联渲染；"手动刷新"按钮复用同一函数。规则为静态误报。
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load()
  }, [load])

  useEffect(() => () => activeRequestRef.current?.abort(), [])

  return (
    <section className="admin-log-console" aria-label="管理员日志控制台">
      <header className="admin-log-header">
        <div>
          <h2 className="admin-log-title">管理员运行日志</h2>
          <p className="admin-log-subtitle">查询关键请求、智能体调用和业务动作审计记录</p>
        </div>
        <div className="admin-log-actions">
          {!loading && !error && (
            <span className="admin-log-count">共 {total} 条记录</span>
          )}
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
            onChange={e => {
              setLevel(e.target.value)
              setOffset(0)
            }}
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
            onChange={e => {
              setCategory(e.target.value)
              setOffset(0)
            }}
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
        <>
        <div className="admin-log-table-wrap">
          <table className="admin-log-table">
            <thead>
              <tr>
                <th>本地时间</th>
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
                  <td className="admin-log-cell-category">{item.category}</td>
                  <td className="admin-log-cell-event" title={item.event}>{item.event}</td>
                  <td className="admin-log-cell-trace">{item.traceId || ''}</td>
                  <td className="admin-log-cell-message" title={item.message}>{item.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <nav className="admin-log-pagination" aria-label="日志分页">
          <button
            type="button"
            onClick={() => setOffset(current => Math.max(0, current - PAGE_SIZE))}
            disabled={loading || offset === 0}
          >
            上一页
          </button>
          <span>第 {Math.floor(offset / PAGE_SIZE) + 1} 页</span>
          <button
            type="button"
            onClick={() => setOffset(current => current + PAGE_SIZE)}
            disabled={loading || !hasMore}
          >
            下一页
          </button>
        </nav>
        </>
      )}
    </section>
  )
}
