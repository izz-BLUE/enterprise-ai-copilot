import './Sidebar.css'
import UiIcon from './UiIcon'

const ENVIRONMENT_LABEL = import.meta.env.VITE_ENVIRONMENT_LABEL
  || (import.meta.env.DEV ? '本地开发环境' : '生产环境')

const MODES = [
  {
    key: 'agent',
    label: '智能体问答',
    desc: '知识查询、任务协助与人工确认',
    icon: 'sparkles',
  },
  {
    key: 'rag',
    label: '标准问答',
    desc: '企业制度与知识库检索',
    icon: 'book-open',
  },
]

export default function Sidebar({
  mode,
  onModeChange,
  loading,
  userRole,
  onAdminLogsOpen,
  showAdminLogs,
  onMockOaOpen,
  showMockOa,
}) {
  const isAdmin = userRole === 'ADMIN'
  const showAdminView = showAdminLogs || showMockOa
  return (
    <aside className="sidebar">
      <div className="sidebar-header">
        <div className="sidebar-logo">
          <span className="logo-icon"><UiIcon name="hexagon" size={24} /></span>
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
            aria-pressed={mode === m.key && !showAdminView}
            data-mode={m.key}
            className={`nav-item ${mode === m.key && !showAdminView ? 'active' : ''}`}
            onClick={() => onModeChange(m.key)}
            disabled={loading}
          >
            <span className="nav-icon"><UiIcon name={m.icon} size={19} /></span>
            <div className="nav-content">
              <span className="nav-label">{m.label}</span>
              <span className="nav-desc">{m.desc}</span>
            </div>
            {mode === m.key && !showAdminView && <span className="nav-active-dot" />}
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
              <span className="nav-icon"><UiIcon name="clipboard-list" size={19} /></span>
              <div className="nav-content">
                <span className="nav-label">日志控制台</span>
                <span className="nav-desc">管理员运行日志查询</span>
              </div>
              {showAdminLogs && <span className="nav-active-dot" />}
            </button>
            <button
              type="button"
              aria-label="模拟 OA 审批"
              aria-pressed={showMockOa}
              data-mode="mock-oa"
              className={`nav-item ${showMockOa ? 'active' : ''}`}
              onClick={() => onMockOaOpen()}
              disabled={loading}
            >
              <span className="nav-icon"><UiIcon name="calendar-check" size={19} /></span>
              <div className="nav-content">
                <span className="nav-label">模拟 OA 审批</span>
                <span className="nav-desc">处理外部报销审批</span>
              </div>
              {showMockOa && <span className="nav-active-dot" />}
            </button>
          </>
        )}
      </nav>

      <div className="sidebar-footer">
        <div className="env-badge">
          <span className="env-dot" />
          <span>{ENVIRONMENT_LABEL}</span>
        </div>
        <div className="env-info">
          环境标识（非实时健康状态）
        </div>
      </div>
    </aside>
  )
}
