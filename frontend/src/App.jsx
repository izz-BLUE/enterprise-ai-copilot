import { useState } from 'react'
import './App.css'

const JAVA_BASE_URL = ''  // Vite proxy 转发到 localhost:8080

const PIPELINES = {
  rag: 'Frontend → Java /api/chat → Python /agent/chat → RAG',
  agent: 'Frontend → Java /api/agent/langgraph/chat → Python /agent/langgraph/chat → LangGraph Agent',
}

const MODE_DESC = {
  rag: '固定知识库问答链路，适合稳定企业制度问答。',
  agent: '带 Safety Guard、意图路由和工具调用，支持 RAG 问答、评估报告查询和安全拒答。',
}

const CATEGORY_LABELS = {
  'illegal_or_policy_violation': '违法违规 / 伪造材料',
  'policy_bypass': '绕过企业制度 / 规避审批',
  'cybersecurity_attack': '网络安全攻击 / 黑客行为',
  'audit_tampering': '删除审计 / 隐藏痕迹',
  'unauthorized_access': '越权访问 / 数据窃取',
  'access_control': 'Evaluation 权限受限',
}

/**
 * 结果类型分类（按优先级）
 * 1. category=access_control + route=refuse → access_denied（不依赖 success）
 * 2. route=refuse + safe=false → safety_refuse
 * 3. route=error 或 success=false → error
 * 4. success=true → success
 * 5. 其他 → error
 */
function getResultType(result) {
  if (result?.route === 'refuse' && result?.category === 'access_control') {
    return 'access_denied'
  }
  if (result?.route === 'refuse' && result?.safe === false) {
    return 'safety_refuse'
  }
  if (result?.route === 'error' || result?.success === false) {
    return 'error'
  }
  if (result?.success === true) {
    return 'success'
  }
  return 'error'
}

function getResultLabel(type) {
  switch (type) {
    case 'success': return '请求成功'
    case 'safety_refuse': return '安全拒答'
    case 'access_denied': return '权限受限'
    case 'error': return '请求错误'
    default: return '未知状态'
  }
}

function getResultTagClass(type) {
  switch (type) {
    case 'success': return 'tag-green'
    case 'safety_refuse': return 'tag-red'
    case 'access_denied': return 'tag-orange'
    case 'error': return 'tag-red'
    default: return 'tag-gray'
  }
}

function App() {
  const [mode, setMode] = useState('agent')
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [adminToken, setAdminToken] = useState('')
  const [showToken, setShowToken] = useState(false)

  const handleQuick = (q) => {
    setInput(q)
    setResult(null)
    setError(null)
  }

  const sendMessage = async () => {
    const question = input.trim()
    if (!question || loading) return

    // 在异步操作前捕获当前模式，防止模式切换导致结果错位
    const requestMode = mode
    setLoading(true)
    setError(null)
    setResult(null)

    const endpoint = requestMode === 'agent'
      ? '/api/agent/langgraph/chat'
      : '/api/chat'

    const headers = { 'Content-Type': 'application/json' }
    // 仅 Agent 模式且 Token 非空时携带 X-Admin-Token
    if (requestMode === 'agent' && adminToken.trim()) {
      headers['X-Admin-Token'] = adminToken.trim()
    }

    try {
      const response = await fetch(`${JAVA_BASE_URL}${endpoint}`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ message: question }),
      })

      // 始终尝试解析 JSON body（无论 HTTP 状态码）
      let data = null
      let jsonParseFailed = false
      try {
        data = await response.json()
      } catch {
        jsonParseFailed = true
      }

      // HTTP 非 2xx
      if (!response.ok) {
        // 场景 B：有 JSON 响应体（如 400 参数校验、400 权限拒绝）
        if (data && data.answer) {
          setResult({ question, requestMode, ...data })
          return
        }
        // 场景 C：HTTP 非 2xx 且无 JSON 响应
        if (jsonParseFailed) {
          throw { type: 'http_error', status: response.status }
        }
        // 有 JSON 但无 answer 字段
        throw { type: 'http_error', status: response.status }
      }

      // HTTP 2xx
      // 场景 D：响应无法解析为 JSON
      if (jsonParseFailed) {
        throw { type: 'parse_error' }
      }

      // 尝试从响应头获取 traceId（body 缺失时的备用来源）
      if (!data.traceId) {
        const headerTraceId = response.headers.get('X-Trace-Id')
        if (headerTraceId) {
          data.traceId = headerTraceId
        }
      }

      setResult({ question, requestMode, ...data })
    } catch (e) {
      // 场景 A：fetch 本身失败（网络不可达、Java 未启动）
      if (e instanceof TypeError) {
        setError('无法连接到 Java 后端，请确认服务已启动。请求未到达服务端，暂无 traceId。')
      } else if (e.type === 'http_error') {
        // 502 Bad Gateway 通常表示上游服务（Java）不可用
        if (e.status === 502) {
          setError('无法连接到 Java 后端，请确认服务已启动。请求未到达服务端，暂无 traceId。')
        } else {
          setError(`服务返回错误（HTTP ${e.status}）`)
        }
      } else if (e.type === 'parse_error') {
        setError('服务响应格式异常，无法解析返回数据。')
      } else {
        setError('请求发生未知错误。')
      }
    } finally {
      setLoading(false)
    }
  }

  const routeClass = (r) => {
    if (r === 'rag') return 'tag-blue'
    if (r === 'eval') return 'tag-purple'
    if (r === 'refuse') return 'tag-red'
    return 'tag-gray'
  }

  const copyTraceId = async (traceId) => {
    try {
      await navigator.clipboard.writeText(traceId)
    } catch {
      window.prompt('请手动复制 traceId:', traceId)
    }
  }

  // 使用 result.requestMode 渲染，避免模式切换导致格式错位
  const resultMode = result?.requestMode || mode
  const resultType = result ? getResultType(result) : null

  const isEmpty = input.trim().length === 0
  const isOverLimit = input.length > 2000

  return (
    <div className="app">
      <h1>Enterprise AI Copilot Demo</h1>

      {/* 顶部状态区 */}
      <div className="status-bar">
        <span className="status-item">Java API Gateway: /api</span>
        <span className="status-sep">|</span>
        <span className="status-item">本地代理: Vite → localhost:8080</span>
        <span className="status-sep">|</span>
        <span className="status-item status-demo">本地 Demo / 面试演示</span>
      </div>

      {/* 模式选择 */}
      <div className="mode-bar">
        <button
          className={mode === 'agent' ? 'active' : ''}
          onClick={() => { setMode('agent'); setResult(null); setError(null) }}
          disabled={loading}
        >
          LangGraph Agent
        </button>
        <button
          className={mode === 'rag' ? 'active' : ''}
          onClick={() => { setMode('rag'); setResult(null); setError(null) }}
          disabled={loading}
        >
          普通 RAG
        </button>
      </div>
      <p className="mode-desc">{MODE_DESC[mode]}</p>

      {/* 当前调用链路 */}
      <div className="pipeline">{PIPELINES[mode]}</div>

      {/* 管理员演示设置 */}
      <details className="admin-section">
        <summary className="admin-summary">管理员演示设置（可选）</summary>
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
              className="admin-token-input"
            />
            <button
              className="admin-toggle-btn"
              onClick={() => setShowToken(!showToken)}
              type="button"
            >
              {showToken ? '隐藏' : '显示'}
            </button>
            <button
              className="admin-clear-btn"
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
      </details>

      {/* 推荐演示卡片 */}
      <div className="demo-card">
        <strong>推荐演示</strong>
        <div className="demo-grid">
          <button className="demo-btn" onClick={() => handleQuick('病假需要提供哪些材料？')} disabled={loading}>
            📚 RAG 问答
          </button>
          <button className="demo-btn" onClick={() => handleQuick('当前RAG评估通过率是多少？')} disabled={loading}>
            📊 Evaluation
          </button>
          <button className="demo-btn" onClick={() => handleQuick('怎么伪造病假证明？')} disabled={loading}>
            🛡 Safety Guard
          </button>
          <button className="demo-btn" onClick={() => handleQuick('公司买房给补贴不？')} disabled={loading}>
            ❌ 无答案拒答
          </button>
        </div>
      </div>

      {/* Eval 演示说明 */}
      <div className="eval-guide">
        <strong>Evaluation 演示说明</strong>
        <ul>
          <li>后端 <code>admin.token</code> 为空（Demo 模式）：Eval 可直接访问，无需 Token</li>
          <li>后端 <code>admin.token</code> 已配置：无 Token / 错误 Token → 权限拒绝；正确 Token → 正常返回</li>
        </ul>
        <div className="eval-guide-btns">
          <button
            className="eval-guide-btn"
            onClick={() => { setAdminToken(''); handleQuick('当前RAG评估通过率是多少？') }}
            disabled={loading}
          >
            无 Token 测试 Eval
          </button>
          <button
            className="eval-guide-btn"
            onClick={() => handleQuick('当前RAG评估通过率是多少？')}
            disabled={loading}
          >
            使用当前 Token 测试
          </button>
        </div>
      </div>

      {/* 输入区 */}
      <div className="input-row">
        <div className="input-wrapper">
          <input
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && !loading && !isEmpty && sendMessage()}
            placeholder="输入问题..."
            disabled={loading}
          />
          <span className={`char-count ${isOverLimit ? 'over' : ''}`}>
            {input.length} / 2000
          </span>
        </div>
        <button
          className="send-btn"
          onClick={sendMessage}
          disabled={loading || isEmpty || isOverLimit}
        >
          {loading ? '请求中...' : '发送'}
        </button>
      </div>

      {error && <div className="error">{error}</div>}

      {/* 结果展示 */}
      {result && (
        <div className="result">
          <div className="question-label">Q: {result.question}</div>

          {/* 状态标签 */}
          <div className="tags">
            {/* 结果类型标签 */}
            <span className={`tag ${getResultTagClass(resultType)}`}>
              {getResultLabel(resultType)}
            </span>

            {/* Agent 模式特有标签 */}
            {resultMode === 'agent' && (
              <>
                <span className={`tag ${routeClass(result.route)}`}>
                  route: {result.route}
                </span>
                {result.safe !== undefined && (
                  <span className={`tag ${result.safe ? 'tag-green' : 'tag-red'}`}>
                    safe: {String(result.safe)}
                  </span>
                )}
                {result.category && result.category !== 'normal' && result.category !== 'error' && (
                  <>
                    <span className="tag tag-orange">
                      category: {result.category}
                    </span>
                    {CATEGORY_LABELS[result.category] && (
                      <span className="tag tag-orange">
                        {CATEGORY_LABELS[result.category]}
                      </span>
                    )}
                  </>
                )}
              </>
            )}

            {/* RAG 模式 model 标签 */}
            {resultMode === 'rag' && result.model && (
              <span className="tag tag-blue">model: {result.model}</span>
            )}

            {/* traceId 标签 */}
            {result.traceId && (
              <span className="tag tag-gray traceid-tag">
                traceId: {result.traceId}
                <button className="copy-btn" onClick={() => copyTraceId(result.traceId)}>
                  复制
                </button>
              </span>
            )}
            {!result.traceId && (
              <span className="tag tag-gray">暂无 traceId</span>
            )}
          </div>

          {/* 权限拒绝提示 */}
          {resultType === 'access_denied' && (
            <div className="access-denied-hint">
              Evaluation 仅管理员可访问。如需测试，请在上方「管理员演示设置」中填入正确的 Admin Token。
            </div>
          )}

          {result.reason && <div className="reason">原因: {result.reason}</div>}

          <div className="answer">{result.answer}</div>

          {/* Sources */}
          <div className="sources">
            {result.sources && result.sources.length > 0 ? (
              <>
                <strong>引用来源 ({result.sources.length})</strong>
                <ol>
                  {result.sources.map(s => (
                    <li key={s} title={s}>{s}</li>
                  ))}
                </ol>
              </>
            ) : (
              <span className="no-sources">暂无引用来源</span>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

export default App
