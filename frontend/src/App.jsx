import { useState, useRef, useEffect } from 'react'
import Sidebar from './components/Sidebar'
import InfoPanel from './components/InfoPanel'
import WelcomeScreen from './components/WelcomeScreen'
import ChatMessage, { UserMessage, LoadingMessage } from './components/ChatMessage'
import ChatInput from './components/ChatInput'
import AdminPanel from './components/AdminPanel'
import './App.css'

const JAVA_BASE_URL = ''  // Vite proxy 转发到 localhost:8080

function App() {
  const [mode, setMode] = useState('agent')
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [messages, setMessages] = useState([])  // { type: 'user'|'assistant', question, result, resultMode }
  const [adminToken, setAdminToken] = useState('')
  const [error, setError] = useState(null)
  const chatEndRef = useRef(null)

  // 自动滚动到底部
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  const handleQuickQuestion = (q) => {
    setInput(q)
    setError(null)
  }

  const handleModeChange = (newMode) => {
    setMode(newMode)
    setMessages([])
    setError(null)
  }

  const sendMessage = async () => {
    const question = input.trim()
    if (!question || loading) return

    const requestMode = mode
    setLoading(true)
    setError(null)
    setInput('')

    // 添加用户消息
    setMessages(prev => [...prev, { type: 'user', question }])

    const endpoint = requestMode === 'agent'
      ? '/api/agent/langgraph/chat'
      : '/api/chat'

    const headers = { 'Content-Type': 'application/json' }
    if (requestMode === 'agent' && adminToken.trim()) {
      headers['X-Admin-Token'] = adminToken.trim()
    }

    try {
      const response = await fetch(`${JAVA_BASE_URL}${endpoint}`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ message: question }),
      })

      let data = null
      let jsonParseFailed = false
      try {
        data = await response.json()
      } catch {
        jsonParseFailed = true
      }

      if (!response.ok) {
        if (data && data.answer) {
          setMessages(prev => [...prev, {
            type: 'assistant',
            question,
            result: { question, requestMode, ...data },
            resultMode: requestMode,
          }])
          return
        }
        if (jsonParseFailed) {
          throw { type: 'http_error', status: response.status }
        }
        throw { type: 'http_error', status: response.status }
      }

      if (jsonParseFailed) {
        throw { type: 'parse_error' }
      }

      if (!data.traceId) {
        const headerTraceId = response.headers.get('X-Trace-Id')
        if (headerTraceId) {
          data.traceId = headerTraceId
        }
      }

      setMessages(prev => [...prev, {
        type: 'assistant',
        question,
        result: { question, requestMode, ...data },
        resultMode: requestMode,
      }])
    } catch (e) {
      if (e instanceof TypeError) {
        setError('无法连接到 Java 后端，请确认服务已启动。')
      } else if (e.type === 'http_error') {
        if (e.status === 502) {
          setError('无法连接到 Java 后端，请确认服务已启动。')
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

  // 获取最后一条助手消息的结果用于右侧面板
  const lastResult = [...messages].reverse().find(m => m.type === 'assistant')?.result
  const lastResultMode = [...messages].reverse().find(m => m.type === 'assistant')?.resultMode

  return (
    <div className="app-layout">
      <Sidebar mode={mode} onModeChange={handleModeChange} loading={loading} />

      <main className="main-area">
        <div className="main-header">
          <div className="header-left">
            {/* 移动端模式切换 - 仅在侧边栏隐藏时显示 */}
            <div className="mobile-mode-switch">
              <select
                className="mode-select"
                value={mode}
                onChange={e => handleModeChange(e.target.value)}
                disabled={loading}
              >
                <option value="agent">🤖 智能体问答</option>
                <option value="rag">📚 标准问答</option>
              </select>
            </div>
            <h2 className="header-title">
              {mode === 'agent' ? '智能体问答' : '标准问答'}
            </h2>
            <span className="header-badge">
              {mode === 'agent' ? 'LangGraph Agent' : 'RAG Pipeline'}
            </span>
          </div>
          {messages.length > 0 && (
            <button
              className="clear-btn"
              onClick={() => { setMessages([]); setError(null) }}
              disabled={loading}
            >
              清空会话
            </button>
          )}
        </div>

        <div className="chat-area">
          {messages.length === 0 ? (
            <WelcomeScreen
              onQuickQuestion={handleQuickQuestion}
              loading={loading}
              mode={mode}
            />
          ) : (
            <div className="messages-list">
              {messages.map((msg, i) => (
                msg.type === 'user'
                  ? <UserMessage key={i} question={msg.question} />
                  : <ChatMessage key={i} result={msg.result} resultMode={msg.resultMode} />
              ))}
              {loading && <LoadingMessage />}
              {error && (
                <div className="error-banner">
                  <span className="error-icon">⚠</span>
                  <span>{error}</span>
                </div>
              )}
              <div ref={chatEndRef} />
            </div>
          )}
        </div>

        <div className="input-section">
          <AdminPanel adminToken={adminToken} setAdminToken={setAdminToken} />
          <ChatInput
            input={input}
            setInput={setInput}
            onSend={sendMessage}
            loading={loading}
          />
        </div>
      </main>

      <InfoPanel
        result={lastResult}
        resultMode={lastResultMode || mode}
      />
    </div>
  )
}

export default App
