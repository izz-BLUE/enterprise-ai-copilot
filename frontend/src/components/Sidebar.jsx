import './Sidebar.css'

const IS_PUBLIC_DEMO = typeof window !== 'undefined'
  && window.location.hostname === 'copilot.jintianchi.cn'

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

export default function Sidebar({ mode, onModeChange, loading, userRole, onAdminLogsOpen, showAdminLogs }) {
  const isAdmin = userRole === 'ADMIN'
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
            type="button"
            aria-label={`切换到${m.label}`}
            aria-pressed={mode === m.key && !showAdminLogs}
            data-mode={m.key}
            className={`nav-item ${mode === m.key && !showAdminLogs ? 'active' : ''}`}
            onClick={() => onModeChange(m.key)}
            disabled={loading}
          >
            <span className="nav-icon">{m.icon}</span>
            <div className="nav-content">
              <span className="nav-label">{m.label}</span>
              <span className="nav-desc">{m.desc}</span>
            </div>
            {mode === m.key && !showAdminLogs && <span className="nav-active-dot" />}
          </button>
        ))}

        {isAdmin && (
          <>
            <div className="nav-section-label">管理工具</div>
            <button
              type="button"
              aria-label="日志控制台"
              aria-pressed={showAdminLogs}
              data-mode="admin-logs"
              className={`nav-item ${showAdminLogs ? 'active' : ''}`}
              onClick={() => onAdminLogsOpen()}
              disabled={loading}
            >
              <span className="nav-icon">📋</span>
              <div className="nav-content">
                <span className="nav-label">日志控制台</span>
                <span className="nav-desc">管理员运行日志查询</span>
              </div>
              {showAdminLogs && <span className="nav-active-dot" />}
            </button>
          </>
        )}
      </nav>

      <div className="sidebar-footer">
        <div className="env-badge">
          <span className="env-dot" />
          <span>{IS_PUBLIC_DEMO ? '公网演示环境' : '本地 Demo 环境'}</span>
        </div>
        <div className="env-info">
          {IS_PUBLIC_DEMO ? 'HTTPS → Nginx → Java → Python' : 'Java :8080 → Python :8000'}
        </div>
      </div>
    </aside>
  )
}
