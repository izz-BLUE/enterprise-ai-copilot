import { useState } from 'react'
import UiIcon from './UiIcon'
import './InfoPanel.css'

const PIPELINE_MAP = {
  rag: [
    { label: 'Web', detail: 'React' },
    { label: 'Gateway', detail: 'Java /api/chat' },
    { label: 'AI Service', detail: 'Python /agent/chat' },
  ],
  agent: [
    { label: 'Web', detail: 'React' },
    { label: 'Gateway', detail: 'Java /api/agent/langgraph/chat' },
    { label: 'AI Service', detail: 'Python /agent/langgraph/chat' },
  ],
}

const ACTION_STATUS_LABELS = {
  pending: '等待确认',
  confirming: '正在提交',
  cancelling: '正在取消',
  succeeded: '已提交',
  cancelled: '已取消',
  expired: '已过期',
  error: '处理失败',
}

function CopyButton({ text }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      window.prompt('请手动复制:', text)
    }
  }

  return (
    <button className="copy-btn-sm" onClick={handleCopy} title="复制">
      {copied ? '✓' : '复制'}
    </button>
  )
}

function MetaItem({ label, value, mono, copyable, tone }) {
  if (value === undefined || value === null) return null
  return (
    <div className="meta-item">
      <span className="meta-label">{label}</span>
      <span className={`meta-value ${mono ? 'mono' : ''} ${tone ? `tone-${tone}` : ''}`}>
        {String(value)}
        {copyable && <CopyButton text={String(value)} />}
      </span>
    </div>
  )
}

function InfoCardHeader({ icon, title, caption }) {
  return (
    <div className="info-card-header">
      <span className="info-card-icon"><UiIcon name={icon} size={17} /></span>
      <div>
        <h3 className="info-card-title">{title}</h3>
        {caption && <p className="info-card-caption">{caption}</p>}
      </div>
    </div>
  )
}

export default function InfoPanel({ result, resultMode, actionUi, compact = false }) {
  const pipeline = PIPELINE_MAP[resultMode] || PIPELINE_MAP.rag
  const sources = Array.isArray(result?.sources) ? result.sources : []

  return (
    <aside className={`info-panel${compact ? ' inline-details' : ''}`} aria-label="回答辅助信息">
      <div className="info-panel-heading">
        <span className="info-panel-kicker">ANSWER CONTEXT</span>
        <h2>回答辅助信息</h2>
        <p>查看本次回答的状态与依据</p>
      </div>

      <div className="info-card info-card-overview">
        <InfoCardHeader icon="info" title="回答概览" />
        {result ? (
          <div className="meta-list">
            <MetaItem
              label="状态"
              value={result.success ? '回答完成' : '处理失败'}
              tone={result.success ? 'success' : 'error'}
            />
            <MetaItem label="模式" value={resultMode === 'agent' ? '智能体问答' : '知识库问答'} />
            <MetaItem
              label="业务动作"
              value={result.pendingAction?.type === 'ANNUAL_LEAVE_REQUEST' ? '年假申请' : undefined}
            />
            <MetaItem label="动作状态" value={ACTION_STATUS_LABELS[actionUi?.phase]} />
            <MetaItem
              label="安全检查"
              value={result.safe !== undefined ? (result.safe ? '已通过' : '已拦截') : undefined}
              tone={result.safe === false ? 'error' : undefined}
            />
          </div>
        ) : (
          <div className="info-empty">
            <UiIcon name="sparkles" size={22} />
            <span>提交问题后，这里会显示回答状态。</span>
          </div>
        )}
      </div>

      <div className="info-card">
        <InfoCardHeader
          icon="sources"
          title="引用来源"
          caption={sources.length > 0 ? `${sources.length} 条参考依据` : undefined}
        />
        {sources.length > 0 ? (
          <div className="sources-list">
            {sources.map((source, index) => (
              <div key={`${source}-${index}`} className="source-item">
                <span className="source-index">{index + 1}</span>
                <span className="source-name" title={source}>{source}</span>
              </div>
            ))}
          </div>
        ) : (
          <div className="info-empty compact">
            <span>{result ? '本次回答未引用知识库文档' : '回答完成后显示参考来源'}</span>
          </div>
        )}
      </div>

      <details className="technical-details">
        <summary>
          <span className="technical-summary-icon"><UiIcon name="code" size={16} /></span>
          <span>技术详情</span>
          <span className="technical-summary-hint">按需查看</span>
        </summary>
        <div className="technical-body">
          {result && (
            <div className="technical-meta">
              <MetaItem label="路由" value={result.route} mono />
              <MetaItem label="模型" value={result.model} mono />
              <MetaItem label="traceId" value={result.traceId} mono copyable />
            </div>
          )}
          <div className="pipeline-chain">
            {pipeline.map((step, index) => (
              <div key={step.label} className="pipeline-step">
                <span className="pipeline-index">{index + 1}</span>
                <div className="pipeline-node">
                  <span className="pipeline-label">{step.label}</span>
                  <span className="pipeline-detail">{step.detail}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      </details>
    </aside>
  )
}
