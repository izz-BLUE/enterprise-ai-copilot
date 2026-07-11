import './Sidebar.css'

const MODES = [
  {
    key: 'agent',
    label: '智能体问答',
    desc: 'Safety Guard + 意图路由 + 工具调用',
    icon: '🤖',
  },
  {
    key: 'rag',
    label: '标准问答',
    desc: '固定知识库 RAG 检索问答',
    icon: '📚',
  },
]

export default function Sidebar({ mode, onModeChange, loading }) {
  return (
    <aside className="sidebar">
      <div className="sidebar-header">
        <div className="sidebar-logo">
          <span className="logo-icon">⬡</span>
          <div className="logo-text">
            <h1 className="logo-title">Enterprise AI Copilot</h1>
            <p className="logo-subtitle">企业知识问答与智能体工作台</p>
          </div>
        </div>
      </div>

      <nav className="sidebar-nav">
        <div className="nav-section-label">问答模式</div>
        {MODES.map(m => (
          <button
            key={m.key}
            className={`nav-item ${mode === m.key ? 'active' : ''}`}
            onClick={() => onModeChange(m.key)}
            disabled={loading}
          >
            <span className="nav-icon">{m.icon}</span>
            <div className="nav-content">
              <span className="nav-label">{m.label}</span>
              <span className="nav-desc">{m.desc}</span>
            </div>
            {mode === m.key && <span className="nav-active-dot" />}
          </button>
        ))}
      </nav>

      <div className="sidebar-footer">
        <div className="env-badge">
          <span className="env-dot" />
          <span>本地 Demo 环境</span>
        </div>
        <div className="env-info">
          Java :8080 → Python :8000
        </div>
      </div>
    </aside>
  )
}
