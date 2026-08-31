import { useCallback, useEffect, useRef, useState } from 'react'
import { AuthExpiredError } from '../services/authApi'
import {
  decideMockOaApproval,
  listMockOaApprovals,
  MockOaApprovalApiError,
} from '../services/mockOaApprovalApi'
import './MockOaApprovalConsole.css'

const STATUS_LABELS = {
  PENDING: '待审批',
  APPROVED: '已批准',
  REJECTED: '已拒绝',
}

function formatAmount(value) {
  const amount = Number(value)
  if (!Number.isFinite(amount)) return '—'
  return `¥${amount.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function formatTimestamp(value) {
  if (!value) return '—'
  const date = new Date(value)
  if (!Number.isFinite(date.getTime())) return String(value)
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).format(date)
}

export default function MockOaApprovalConsole({ accessToken, onBackToChat, onAuthenticationExpired }) {
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [actionBusy, setActionBusy] = useState(null)
  const activeRequestRef = useRef(null)
  const requestSequenceRef = useRef(0)

  const showRequestError = useCallback((requestError) => {
    if (requestError instanceof AuthExpiredError) {
      onAuthenticationExpired?.()
      setError('登录状态已失效，请重新登录。')
      return
    }
    if (requestError instanceof MockOaApprovalApiError) {
      setError(requestError.message)
      return
    }
    setError('请求失败，请稍后重试。')
  }, [onAuthenticationExpired])

  const load = useCallback(async () => {
    activeRequestRef.current?.abort()
    const controller = new AbortController()
    activeRequestRef.current = controller
    const sequence = requestSequenceRef.current + 1
    requestSequenceRef.current = sequence
    setLoading(true)
    setError(null)
    try {
      const data = await listMockOaApprovals({ accessToken, signal: controller.signal })
      if (sequence === requestSequenceRef.current) {
        setItems(Array.isArray(data?.items) ? data.items : [])
      }
      return true
    } catch (requestError) {
      if (requestError?.name === 'AbortError') return false
      if (sequence === requestSequenceRef.current) {
        showRequestError(requestError)
        setItems([])
      }
      return false
    } finally {
      if (sequence === requestSequenceRef.current) {
        setLoading(false)
        activeRequestRef.current = null
      }
    }
  }, [accessToken, showRequestError])

  useEffect(() => {
    // 异步加载完成后更新列表，首次进入审批台时自动读取最新状态。
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load()
  }, [load])

  useEffect(() => () => activeRequestRef.current?.abort(), [])

  const handleDecision = async (item, decision) => {
    if (actionBusy || item.status !== 'PENDING') return
    setActionBusy({ requestId: item.requestId, decision })
    setError(null)
    setNotice(null)
    try {
      const result = await decideMockOaApproval({
        accessToken,
        requestId: item.requestId,
        decision,
      })
      const statusLabel = decision === 'APPROVED' ? '已批准' : '已拒绝'
      setItems(current => current.map(record => record.requestId === item.requestId
        ? { ...record, status: result.status || decision }
        : record))
      setNotice(`Mock OA ${statusLabel}，该结果将通过 webhook 异步同步到业务系统。`)
      await load()
    } catch (requestError) {
      showRequestError(requestError)
    } finally {
      setActionBusy(null)
    }
  }

  return (
    <section className="mock-oa-console" aria-label="模拟 OA 审批台">
      <header className="mock-oa-header">
        <div>
          <h2 className="mock-oa-title">模拟 OA 审批</h2>
          <p className="mock-oa-subtitle">查看并处理等待外部审批的差旅报销申请</p>
        </div>
        <div className="mock-oa-actions">
          {!loading && !error && <span className="mock-oa-count">共 {items.length} 条记录</span>}
          <button
            type="button"
            className="mock-oa-refresh"
            onClick={load}
            disabled={loading || actionBusy !== null}
            aria-label="刷新审批列表"
          >
            {loading ? '刷新中…' : '刷新'}
          </button>
          {onBackToChat && (
            <button type="button" className="mock-oa-back" onClick={onBackToChat} aria-label="返回聊天">
              返回聊天
            </button>
          )}
        </div>
      </header>

      {notice && <p className="mock-oa-notice" role="status">{notice}</p>}
      {error && <p className="mock-oa-error" role="alert">{error}</p>}
      {!error && !loading && items.length === 0 && (
        <p className="mock-oa-empty" role="status">暂无审批记录</p>
      )}

      {items.length > 0 && (
        <div className="mock-oa-list">
          {items.map(item => {
            const isBusy = actionBusy?.requestId === item.requestId
            const statusLabel = STATUS_LABELS[item.status] || item.status || '未知状态'
            return (
              <article className="mock-oa-card" key={item.requestId}>
                <header className="mock-oa-card-header">
                  <div>
                    <h3>报销单：{item.expenseId || '—'}</h3>
                    <p>OA 请求：{item.requestId}</p>
                  </div>
                  <span className={`mock-oa-status mock-oa-status-${item.status}`}>
                    {statusLabel}
                  </span>
                </header>
                <dl className="mock-oa-details">
                  <div><dt>员工</dt><dd>{item.employeeId || '—'}</dd></div>
                  <div><dt>出差</dt><dd>{item.tripId || '—'}</dd></div>
                  <div><dt>成本中心</dt><dd>{item.costCenter || '—'}</dd></div>
                  <div><dt>申报金额</dt><dd>{formatAmount(item.claimedAmount)}</dd></div>
                  <div><dt>可报销</dt><dd>{formatAmount(item.reimbursableAmount)}</dd></div>
                  <div><dt>创建时间</dt><dd>{formatTimestamp(item.createdAt)}</dd></div>
                </dl>
                {item.status === 'PENDING' && (
                  <div className="mock-oa-card-actions">
                    <button
                      type="button"
                      className="mock-oa-approve"
                      onClick={() => handleDecision(item, 'APPROVED')}
                      disabled={actionBusy !== null}
                      aria-label={`批准 ${item.requestId}`}
                    >
                      {isBusy && actionBusy.decision === 'APPROVED' ? '处理中…' : '批准'}
                    </button>
                    <button
                      type="button"
                      className="mock-oa-reject"
                      onClick={() => handleDecision(item, 'REJECTED')}
                      disabled={actionBusy !== null}
                      aria-label={`拒绝 ${item.requestId}`}
                    >
                      {isBusy && actionBusy.decision === 'REJECTED' ? '处理中…' : '拒绝'}
                    </button>
                  </div>
                )}
              </article>
            )
          })}
        </div>
      )}
    </section>
  )
}
