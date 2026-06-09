import { useState } from 'react'
import './App.css'

const JAVA_BASE_URL = ''  // Vite proxy 转发到 localhost:8080

const DEMO_STEPS = [
  { q: '病假需要提供哪些材料？', desc: 'RAG 问答' },
  { q: '当前RAG评估通过率是多少？', desc: 'Eval Tool' },
  { q: '怎么伪造病假证明？', desc: 'Safety Guard' },
]

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
}

function App() {
  const [mode, setMode] = useState('agent')
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)

  const sendMessage = async () => {
    const question = input.trim()
    if (!question) return
    setLoading(true)
    setError(null)
    setResult(null)

    const endpoint = mode === 'agent'
      ? '/api/agent/langgraph/chat'
      : '/api/chat'

    // 生成 traceId
    const traceId = crypto.randomUUID
      ? crypto.randomUUID()
      : Date.now() + '-' + Math.random().toString(36).slice(2)

    try {
      const response = await fetch(`${JAVA_BASE_URL}${endpoint}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Trace-Id': traceId,
        },
        body: JSON.stringify({ message: question }),
      })
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const data = await response.json()
      // 如果后端没有返回 traceId，用前端生成的兜底
      if (!data.traceId) data.traceId = traceId
      setResult({ question, ...data })
    } catch (e) {
      setError(`请求失败: ${e.message} (traceId: ${traceId})`)
    } finally {
      setLoading(false)
    }
  }

  const handleQuick = (q) => { setInput(q); setResult(null); setError(null) }

  const routeClass = (r) => {
    if (r === 'rag') return 'tag-blue'
    if (r === 'eval') return 'tag-purple'
    if (r === 'refuse') return 'tag-red'
    return 'tag-gray'
  }

  return (
    <div className="app">
      <h1>Enterprise AI Copilot Demo</h1>

      {/* 模式选择 */}
      <div className="mode-bar">
        <button className={mode === 'agent' ? 'active' : ''} onClick={() => { setMode('agent'); setResult(null); setError(null) }}>
          LangGraph Agent
        </button>
        <button className={mode === 'rag' ? 'active' : ''} onClick={() => { setMode('rag'); setResult(null); setError(null) }}>
          普通 RAG
        </button>
      </div>
      <p className="mode-desc">{MODE_DESC[mode]}</p>

      {/* 当前调用链路 */}
      <div className="pipeline">{PIPELINES[mode]}</div>

      {/* 推荐演示顺序 */}
      <div className="demo-card">
        <strong>推荐演示顺序</strong>
        <ol className="demo-list">
          {DEMO_STEPS.map((s, i) => (
            <li key={i}>
              <button className="link-btn" onClick={() => handleQuick(s.q)}>{s.q}</button>
              <span className="step-desc">— {s.desc}</span>
            </li>
          ))}
        </ol>
      </div>

      {/* 输入区 */}
      <div className="input-row">
        <input
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && sendMessage()}
          placeholder="输入问题..."
        />
        <button className="send-btn" onClick={sendMessage} disabled={loading}>
          {loading ? '请求中...' : '发送'}
        </button>
      </div>

      {error && <div className="error">{error}</div>}

      {/* 结果展示 */}
      {result && (
        <div className="result">
          <div className="question-label">Q: {result.question}</div>

          {/* Agent 模式标签 */}
          {mode === 'agent' && (
            <div className="tags">
              <span className={`tag ${routeClass(result.route)}`}>
                route: {result.route}
              </span>
              <span className={`tag ${result.safe ? 'tag-green' : 'tag-red'}`}>
                safe: {String(result.safe)}
              </span>
              <span className={`tag ${result.success ? 'tag-green' : 'tag-red'}`}>
                调用: {result.success ? '成功' : '失败'}
              </span>
              {result.category && result.category !== 'normal' && (
                <>
                  <span className="tag tag-orange">category: {result.category}</span>
                  {CATEGORY_LABELS[result.category] && (
                    <span className="tag tag-orange">风险类型: {CATEGORY_LABELS[result.category]}</span>
                  )}
                </>
              )}
              {result.traceId && <span className="tag tag-gray">traceId: {result.traceId}</span>}
            </div>
          )}

          {/* RAG 模式标签 */}
          {mode === 'rag' && (
            <div className="tags">
              <span className={`tag ${result.success ? 'tag-green' : 'tag-red'}`}>
                调用: {result.success ? '成功' : '失败'}
              </span>
              {result.model && <span className="tag tag-blue">model: {result.model}</span>}
              {result.traceId && <span className="tag tag-gray">traceId: {result.traceId}</span>}
            </div>
          )}

          {result.reason && <div className="reason">原因: {result.reason}</div>}

          <div className="answer">{result.answer}</div>

          {/* Sources */}
          <div className="sources">
            {result.sources && result.sources.length > 0 ? (
              <>
                <strong>引用来源 ({result.sources.length})</strong>
                <ol>{result.sources.map(s => <li key={s}>{s}</li>)}</ol>
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
