import { useState } from 'react'
import './AdminPanel.css'

export default function AdminPanel({ adminToken, setAdminToken }) {
  const [showToken, setShowToken] = useState(false)
  const [expanded, setExpanded] = useState(false)

  return (
    <div className="admin-panel">
      <button
        className="admin-toggle"
        onClick={() => setExpanded(!expanded)}
      >
        <span className="admin-toggle-icon">{expanded ? '▾' : '▸'}</span>
        <span className="admin-toggle-label">管理员演示设置</span>
        {adminToken.trim() && <span className="admin-badge">已配置</span>}
      </button>

      {expanded && (
        <div className="admin-body">
          <p className="admin-info">
            Token 只保存在当前页面内存，不会持久化保存。是否具备权限最终由 Java 后端判断。
          </p>
          <div className="admin-input-row">
            <input
              type={showToken ? 'text' : 'password'}
              value={adminToken}
              onChange={e => setAdminToken(e.target.value)}
              placeholder="输入 Admin Token..."
              className="admin-input"
            />
            <button
              className="admin-btn"
              onClick={() => setShowToken(!showToken)}
              type="button"
            >
              {showToken ? '隐藏' : '显示'}
            </button>
            <button
              className="admin-btn admin-btn-clear"
              onClick={() => { setAdminToken(''); setShowToken(false) }}
              type="button"
            >
              清空
            </button>
          </div>
          <p className="admin-status">
            {adminToken.trim()
              ? '已填写管理员 Token；仅 Agent 模式请求携带。'
              : '未提供管理员 Token；服务端是否启用 Demo 模式由后端配置决定。'}
          </p>
        </div>
      )}
    </div>
  )
}
