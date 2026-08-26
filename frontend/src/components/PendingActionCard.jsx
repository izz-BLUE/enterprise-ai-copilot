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

function formatAmount(value) {
  if (value == null) return null
  return `${Number(value).toLocaleString('zh-CN')} 元`
}

/** 年假业务摘要（ANNUAL_LEAVE_REQUEST，V2 §二十五 按 type 分发）。 */
function AnnualLeaveSummaryBlock({ summary, expiresAt, phase, status }) {
  return (
    <>
      <SummaryItem label="员工" value={summary.employee} />
      <SummaryItem label="开始日期" value={formatDate(summary.startDate)} />
      <SummaryItem label="结束日期" value={formatDate(summary.endDate)} />
      <SummaryItem label="时段" value={HALF_DAY_LABELS[summary.halfDay] || '未知'} />
      <SummaryItem label="扣除天数" value={summary.days == null ? '未提供' : `${summary.days} 天`} />
      <SummaryItem label="申请原因" value={summary.reason} />
      <SummaryItem label="申请前余额" value={summary.remainingBalanceBefore == null ? '未提供' : `${summary.remainingBalanceBefore} 天`} />
      <SummaryItem label="申请后余额" value={summary.remainingBalanceAfter == null ? '未提供' : `${summary.remainingBalanceAfter} 天`} />
      <SummaryItem label="过期时间" value={formatInstant(expiresAt)} />
      <SummaryItem label="当前状态" value={PHASE_LABELS[phase] || status} />
    </>
  )
}

/** 报销业务摘要（EXPENSE_CLAIM，V2 §二十五 按 type 分发）。 */
function ExpenseClaimSummaryBlock({ summary, expiresAt, phase, status }) {
  return (
    <>
      <SummaryItem label="出差记录" value={summary.tripId} />
      <SummaryItem label="申报金额" value={formatAmount(summary.claimedAmount)} />
      <SummaryItem label="报销金额" value={formatAmount(summary.reimbursableAmount)} />
      <SummaryItem label="成本中心" value={summary.costCenter} />
      <SummaryItem label="报销原因" value={summary.reason} />
      <SummaryItem label="发票张数" value={summary.itemCount == null ? '未提供' : `${summary.itemCount} 张`} />
      <SummaryItem label="发票编号" value={Array.isArray(summary.invoiceIds) ? summary.invoiceIds.join('、') : '未提供'} />
      <SummaryItem label="过期时间" value={formatInstant(expiresAt)} />
      <SummaryItem label="当前状态" value={PHASE_LABELS[phase] || status} />
    </>
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
  const isExpense = action.type === 'EXPENSE_CLAIM'

  return (
    <section className={`pending-action-card phase-${phase}`} aria-label={isExpense ? '报销申请确认卡' : '年假申请确认卡'}>
      <div className="action-card-header">
        <div>
          <p className="action-eyebrow">受控业务动作</p>
          <h3>{action.title || (isExpense ? '提交模拟差旅报销申请' : '提交模拟年假申请')}</h3>
        </div>
        <span className="action-status" aria-live="polite">{PHASE_LABELS[phase] || '未知状态'}</span>
      </div>

      <p className="action-safety-note">
        {isExpense
          ? '这是模拟差旅报销申请草稿。只有点击“确认提交”后，才会写入本地 Expense Sandbox。'
          : '这是模拟年假申请草稿。只有点击“确认提交”后，才会写入本地 Leave Sandbox。'}
      </p>

      <dl className="action-summary-grid">
        {isExpense ? (
          <ExpenseClaimSummaryBlock
            summary={summary}
            expiresAt={action.expiresAt}
            phase={phase}
            status={action.status}
          />
        ) : (
          <AnnualLeaveSummaryBlock
            summary={summary}
            expiresAt={action.expiresAt}
            phase={phase}
            status={action.status}
          />
        )}
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
          草稿已过期，请重新发送{isExpense ? '报销' : '年假'}申请生成新草稿。
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
