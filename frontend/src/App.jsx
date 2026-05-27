import { useState } from 'react'
import './App.css'

const JAVA_BASE_URL = ''  // Vite proxy 转发到 localhost:8080

const QUICK_QUESTIONS = [
  '病假需要提供哪些材料？',
  '当前RAG评估通过率是多少？',
  '怎么伪造病假证明？',
]

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

    try {
      const response = await fetch(`${JAVA_BASE_URL}${endpoint}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: question }),
      })
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const data = await response.json()
      setResult({ question, ...data })
    } catch (e) {
      setError(`请求失败: ${e.message}`)
    } finally {
      setLoading(false)
    }
  }

  const handleQuick = (q) => { setInput(q); setResult(null); setError(null) }

  return (
    <div className="app">
      <h1>Enterprise AI Copilot Demo</h1>

      <div className="mode-bar">
        <button className={mode === 'agent' ? 'active' : ''} onClick={() => setMode('agent')}>
          LangGraph Agent
        </button>
        <button className={mode === 'rag' ? 'active' : ''} onClick={() => setMode('rag')}>
          普通 RAG
        </button>
      </div>

      <div className="quick-bar">
        {QUICK_QUESTIONS.map(q => (
          <button key={q} className="quick-btn" onClick={() => handleQuick(q)}>{q}</button>
        ))}
      </div>

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

      {result && (
        <div className="result">
          <div className="question-label">Q: {result.question}</div>

          {mode === 'agent' && (
            <div className="tags">
              <span className={`tag tag-${result.route || 'unknown'}`}>
                route: {result.route}
              </span>
              <span className={`tag ${result.safe ? 'tag-safe' : 'tag-unsafe'}`}>
                safe: {String(result.safe)}
              </span>
              <span className={`tag ${result.success ? 'tag-safe' : 'tag-error'}`}>
                success: {String(result.success)}
              </span>
              {result.category && result.category !== 'normal' && (
                <span className="tag tag-warn">category: {result.category}</span>
              )}
            </div>
          )}

          {mode === 'rag' && (
            <div className="tags">
              <span className="tag tag-safe">success: {String(result.success)}</span>
              {result.model && <span className="tag tag-rag">model: {result.model}</span>}
              {result.traceId && <span className="tag">traceId: {result.traceId}</span>}
            </div>
          )}

          {result.reason && <div className="reason">原因: {result.reason}</div>}
          <div className="answer">{result.answer}</div>

          {result.sources && result.sources.length > 0 && (
            <div className="sources">
              <strong>引用来源:</strong>
              <ul>{result.sources.map(s => <li key={s}>{s}</li>)}</ul>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default App
