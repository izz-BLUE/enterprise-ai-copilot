import { useEffect, useRef } from 'react'
import './PendingActionCard.css'

const HALF_DAY_LABELS = {
  NONE: '全天',
  AM: '上午半天',
  PM: '下午半天',
}

const PHASE_LABELS = {
  pending: '等待确认',
  confirming: '正在提交',
  cancelling: '正在取消',
  succeeded: '已提交',
  cancelled: '已取消',
  expired: '已过期',
  error: '处理失败',
}

const MAX_TIMEOUT_MS = 2_147_483_647

function formatDate(date) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date || '')
  return match ? `${match[1]}年${match[2]}月${match[3]}日` : '未提供'
}

function formatInstant(instant) {
  const date = new Date(instant)
  if (Number.isNaN(date.getTime())) return '未知'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(date)
}

function SummaryItem({ label, value }) {
  return (
    <div className="action-summary-item">
      <dt>{label}</dt>
      <dd>{value ?? '未提供'}</dd>
    </div>
  )
}

export default function PendingActionCard({ action, actionUi, onConfirm, onCancel, onExpire }) {
  const onExpireRef = useRef(onExpire)

  useEffect(() => {
    onExpireRef.current = onExpire
  }, [onExpire])

  useEffect(() => {
    if (!['pending', 'error'].includes(actionUi.phase)) return undefined
    const expiresAt = Date.parse(action.expiresAt)
    if (!Number.isFinite(expiresAt)) return undefined

    const remaining = expiresAt - Date.now()
    if (remaining <= 0) {
      onExpireRef.current()
      return undefined
    }

    const timeout = window.setTimeout(
      () => onExpireRef.current(),
      Math.min(remaining, MAX_TIMEOUT_MS),
    )
    return () => window.clearTimeout(timeout)
  }, [action.expiresAt, actionUi.phase])

  const summary = action.summary || {}
  const phase = actionUi.phase
  const busy = phase === 'confirming' || phase === 'cancelling'
  const showPendingActions = phase === 'pending'
  const showRetry = phase === 'error' && actionUi.retryDecision
  const execution = actionUi.execution

  return (
    <section className={`pending-action-card phase-${phase}`} aria-label="年假申请确认卡">
      <div className="action-card-header">
        <div>
          <p className="action-eyebrow">受控业务动作</p>
          <h3>{action.title || '提交模拟年假申请'}</h3>
        </div>
        <span className="action-status" aria-live="polite">{PHASE_LABELS[phase] || '未知状态'}</span>
      </div>

      <p className="action-safety-note">
        这是模拟年假申请草稿。只有点击“确认提交”后，才会写入本地 Leave Sandbox。
      </p>

      <dl className="action-summary-grid">
        <SummaryItem label="员工" value={summary.employee} />
        <SummaryItem label="开始日期" value={formatDate(summary.startDate)} />
        <SummaryItem label="结束日期" value={formatDate(summary.endDate)} />
        <SummaryItem label="时段" value={HALF_DAY_LABELS[summary.halfDay] || '未知'} />
        <SummaryItem label="扣除天数" value={summary.days == null ? '未提供' : `${summary.days} 天`} />
        <SummaryItem label="申请原因" value={summary.reason} />
        <SummaryItem label="申请前余额" value={summary.remainingBalanceBefore == null ? '未提供' : `${summary.remainingBalanceBefore} 天`} />
        <SummaryItem label="申请后余额" value={summary.remainingBalanceAfter == null ? '未提供' : `${summary.remainingBalanceAfter} 天`} />
        <SummaryItem label="过期时间" value={formatInstant(action.expiresAt)} />
        <SummaryItem label="当前状态" value={PHASE_LABELS[phase] || action.status} />
      </dl>

      {phase === 'succeeded' && (
        <div className="action-result action-result-success" aria-live="polite">
          <strong>模拟申请已提交</strong>
          {execution?.requestId && <span>申请编号：{execution.requestId}</span>}
          {execution?.replayed && <span>本次响应为幂等重放，未重复创建申请。</span>}
        </div>
      )}

      {phase === 'cancelled' && (
        <div className="action-result" aria-live="polite">申请草稿已取消</div>
      )}

      {phase === 'expired' && (
        <div className="action-result action-result-warning" aria-live="polite">
          草稿已过期，请重新发送年假申请生成新草稿。
        </div>
      )}

      {actionUi.error && (
        <div className="action-error" role="alert">{actionUi.error}</div>
      )}

      {(showPendingActions || busy || showRetry) && (
        <div className="action-buttons">
          {showRetry ? (
            <button
              type="button"
              className="action-btn action-btn-primary"
              onClick={actionUi.retryDecision === 'confirm' ? onConfirm : onCancel}
            >
              {actionUi.retryDecision === 'confirm' ? '重试确认' : '重试取消'}
            </button>
          ) : (
            <>
              <button
                type="button"
                className="action-btn action-btn-primary"
                onClick={onConfirm}
                disabled={busy}
              >
                {phase === 'confirming' ? '正在提交…' : '确认提交'}
              </button>
              <button
                type="button"
                className="action-btn action-btn-secondary"
                onClick={onCancel}
                disabled={busy}
              >
                {phase === 'cancelling' ? '正在取消…' : '取消草稿'}
              </button>
            </>
          )}
        </div>
      )}
    </section>
  )
}
