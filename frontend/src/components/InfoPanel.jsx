import { useState } from 'react'
import './InfoPanel.css'

const PIPELINE_MAP = {
  rag: [
    { label: 'Frontend', detail: 'React + Vite' },
    { label: 'Gateway', detail: 'Java /api/chat' },
    { label: 'Service', detail: 'Python /agent/chat' },
    { label: 'Engine', detail: 'RAG Pipeline' },
  ],
  agent: [
    { label: 'Frontend', detail: 'React + Vite' },
    { label: 'Gateway', detail: 'Java /api/agent/langgraph/chat' },
    { label: 'Service', detail: 'Python /agent/langgraph/chat' },
    { label: 'Engine', detail: 'LangGraph Agent' },
  ],
}

const CAPABILITIES = [
  { icon: '🔍', title: 'RAG 检索问答', desc: '基于 FAISS 向量索引的企业知识库检索' },
  { icon: '🔗', title: '混合检索', desc: '向量检索 + BM25 关键词检索融合排序' },
  { icon: '🛡', title: 'Safety Guard', desc: '规则路由安全检查，拦截违规请求' },
  { icon: '📊', title: '评估体系', desc: '确定性规则评分，衡量回答质量' },
  { icon: '✅', title: '受控业务动作', desc: '人工确认后执行模拟写操作' },
]

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

function MetaItem({ label, value, mono, copyable }) {
  if (value === undefined || value === null) return null
  return (
    <div className="meta-item">
      <span className="meta-label">{label}</span>
      <span className={`meta-value ${mono ? 'mono' : ''}`}>
        {String(value)}
        {copyable && <CopyButton text={String(value)} />}
      </span>
    </div>
  )
}

export default function InfoPanel({ result, resultMode, actionUi }) {
  const pipeline = PIPELINE_MAP[resultMode] || PIPELINE_MAP.rag

  return (
    <aside className="info-panel">
      {/* 调用链路 */}
      <div className="info-card">
        <div className="info-card-header">
          <span className="info-card-icon">🔗</span>
          <h3 className="info-card-title">调用链路</h3>
        </div>
        <div className="pipeline-chain">
          {pipeline.map((step, i) => (
            <div key={i} className="pipeline-step">
              <div className="pipeline-node">
                <span className="pipeline-label">{step.label}</span>
                <span className="pipeline-detail">{step.detail}</span>
              </div>
              {i < pipeline.length - 1 && (
                <div className="pipeline-arrow">→</div>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* 回答信息 */}
      <div className="info-card">
        <div className="info-card-header">
          <span className="info-card-icon">📋</span>
          <h3 className="info-card-title">最新回答信息</h3>
        </div>
        {result ? (
          <div className="meta-list">
            <MetaItem label="状态" value={result.success ? '成功' : '失败'} />
            <MetaItem label="模式" value={resultMode === 'agent' ? '智能体' : '标准 RAG'} />
            <MetaItem label="路由" value={result.route} />
            <MetaItem
              label="动作类型"
              value={result.pendingAction?.type === 'ANNUAL_LEAVE_REQUEST' ? '年假申请' : undefined}
            />
            <MetaItem label="动作状态" value={ACTION_STATUS_LABELS[actionUi?.phase]} />
            <MetaItem label="安全" value={result.safe !== undefined ? (result.safe ? '通过' : '拦截') : undefined} />
            <MetaItem label="模型" value={result.model} mono />
            <MetaItem label="traceId" value={result.traceId} mono copyable />
          </div>
        ) : (
          <div className="info-empty">发送问题后显示最新回答信息</div>
        )}
      </div>

      {/* 来源引用 */}
      <div className="info-card">
        <div className="info-card-header">
          <span className="info-card-icon">📄</span>
          <h3 className="info-card-title">最新回答来源</h3>
        </div>
        {result?.sources && result.sources.length > 0 ? (
          <div className="sources-list">
            {result.sources.map((s, i) => (
              <div key={i} className="source-item">
                <span className="source-index">{i + 1}</span>
                <span className="source-name" title={s}>{s}</span>
              </div>
            ))}
          </div>
        ) : (
          <div className="info-empty">
            {result ? '本次回答无引用来源' : '发送问题后显示最新回答来源'}
          </div>
        )}
      </div>

      {/* 能力说明 */}
      <div className="info-card">
        <div className="info-card-header">
          <span className="info-card-icon">⚡</span>
          <h3 className="info-card-title">系统能力</h3>
        </div>
        <div className="capabilities-list">
          {CAPABILITIES.map((cap, i) => (
            <div key={i} className="capability-item">
              <span className="capability-icon">{cap.icon}</span>
              <div className="capability-content">
                <span className="capability-title">{cap.title}</span>
                <span className="capability-desc">{cap.desc}</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </aside>
  )
}
