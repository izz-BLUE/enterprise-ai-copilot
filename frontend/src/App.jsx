import { useState, useRef, useEffect } from 'react'
import Sidebar from './components/Sidebar'
import InfoPanel from './components/InfoPanel'
import WelcomeScreen from './components/WelcomeScreen'
import ChatMessage, { UserMessage, LoadingMessage } from './components/ChatMessage'
import ChatInput from './components/ChatInput'
import AdminPanel from './components/AdminPanel'
import {
  BusinessActionApiError,
  cancelBusinessAction,
  confirmBusinessAction,
} from './services/businessActionApi'
import { authenticatedFetch, AuthExpiredError } from './services/authApi'
import './App.css'

const JAVA_BASE_URL = ''  // Vite proxy 转发到 localhost:8080

const TERMINAL_ACTION_ERRORS = new Set([
  'ACTION_EXPIRED',
  'ACTION_NOT_FOUND',
  'INVALID_CONFIRMATION_NONCE',
  'ACTION_STATE_CONFLICT',
  'ACTION_STALE',
  'DEMO_IDENTITY_REQUIRED',
  'DEMO_IDENTITY_INVALID',
  'DEMO_IDENTITY_DISABLED',
])

const RETRYABLE_ACTION_ERRORS = new Set([
  'ADMIN_REQUIRED',
  'ACTION_IN_PROGRESS',
  'ACTION_INTERNAL_ERROR',
  'NETWORK_ERROR',
])

const newMessageId = () => crypto.randomUUID()

// Scoped Conversation Memory / Task Continuity P0 —— conversationId 客户端会话命名空间。
// 1) 不是可信身份，仅作为分组 hint 透传给 Java；Java 会基于 trusted user_id + 本字段做权威解析；
// 2) 仅保存在 sessionStorage：跨刷新保留但关 tab 自动清除；
// 3) logout / 401 时由 clearConversationId() 主动清空。
const CONVERSATION_ID_STORAGE_KEY = 'enterprise-ai-copilot.conversation-id'
const readStoredConversationId = () => {
  try {
    const raw = sessionStorage.getItem(CONVERSATION_ID_STORAGE_KEY)
    if (typeof raw !== 'string' || raw.length === 0 || raw.length > 64) {
      return null
    }
    return raw
  } catch {
    return null
  }
}
const writeStoredConversationId = (id) => {
  try {
    if (id) {
      sessionStorage.setItem(CONVERSATION_ID_STORAGE_KEY, id)
    } else {
      sessionStorage.removeItem(CONVERSATION_ID_STORAGE_KEY)
    }
  } catch {
    // sessionStorage 在隐私模式或被禁用时可能抛错；忽略并继续。
  }
}

function isSupportedPendingAction(action) {
  return action?.type === 'ANNUAL_LEAVE_REQUEST'
    && action?.confirmationRequired === true
}

function initialActionUi(phase = 'pending', error = null) {
  return {
    phase,
    execution: null,
    error,
    retryDecision: null,
  }
}

function App({ authState, onLogout }) {
  const [mode, setMode] = useState('agent')
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [messages, setMessages] = useState([])
  const [adminToken, setAdminToken] = useState('')
  const [error, setError] = useState(null)
  const chatEndRef = useRef(null)
  const actionSecretsRef = useRef(new Map())
  const idempotencyKeysRef = useRef(new Map())
  const actionLocksRef = useRef(new Set())
  // Phase 2 conversationId：从 sessionStorage 恢复，确保刷新后会话命名空间连续。
  // 不是可信身份，仅作为分组 hint 传给 Java。
  const conversationIdRef = useRef(readStoredConversationId())

  const clearConversationId = () => {
    conversationIdRef.current = null
    writeStoredConversationId(null)
  }

  const rememberConversationId = (id) => {
    if (typeof id !== 'string' || id.length === 0 || id.length > 64) {
      return
    }
    conversationIdRef.current = id
    writeStoredConversationId(id)
  }

  const actionBusy = messages.some(message =>
    message.actionUi?.phase === 'confirming' || message.actionUi?.phase === 'cancelling')

  const clearSession = () => {
    setMessages([])
    setInput('')
    setAdminToken('')
    setError(null)
    actionSecretsRef.current.clear()
    idempotencyKeysRef.current.clear()
    actionLocksRef.current.clear()
    clearConversationId()
  }

  // 自动滚动到底部
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  const handleQuickQuestion = (q) => {
    setInput(q)
    setError(null)
  }

  const handleModeChange = (newMode) => {
    if (actionBusy || actionLocksRef.current.size > 0) return
    setMode(newMode)
    setMessages([])
    setError(null)
    actionSecretsRef.current.clear()
    idempotencyKeysRef.current.clear()
    actionLocksRef.current.clear()
  }

  const handleClearMessages = () => {
    if (actionBusy || actionLocksRef.current.size > 0) return
    setMessages([])
    setError(null)
    actionSecretsRef.current.clear()
    idempotencyKeysRef.current.clear()
    actionLocksRef.current.clear()
  }

  const updateActionUi = (messageId, nextUi) => {
    setMessages(prev => prev.map(message => {
      if (message.id !== messageId) return message
      return {
        ...message,
        actionUi: typeof nextUi === 'function' ? nextUi(message.actionUi) : nextUi,
      }
    }))
  }

  const handleActionDecision = async (messageId, decision) => {
    if (actionLocksRef.current.has(messageId)) return

    const message = messages.find(item => item.id === messageId)
    const action = message?.result?.pendingAction
    const secret = actionSecretsRef.current.get(messageId)
    if (!isSupportedPendingAction(action) || !secret?.confirmationNonce) {
      updateActionUi(messageId, initialActionUi('error', '草稿确认信息不可用，请重新生成草稿。'))
      actionSecretsRef.current.delete(messageId)
      idempotencyKeysRef.current.delete(messageId)
      return
    }

    actionLocksRef.current.add(messageId)
    let idempotencyKey = idempotencyKeysRef.current.get(messageId)
    if (decision === 'confirm' && !idempotencyKey) {
      idempotencyKey = crypto.randomUUID()
      idempotencyKeysRef.current.set(messageId, idempotencyKey)
    }

    updateActionUi(messageId, {
      phase: decision === 'confirm' ? 'confirming' : 'cancelling',
      execution: null,
      error: null,
      retryDecision: null,
    })

    try {
      const execution = decision === 'confirm'
        ? await confirmBusinessAction({
            actionId: action.actionId,
            confirmationNonce: secret.confirmationNonce,
            idempotencyKey,
            adminToken,
            accessToken: authState.accessToken,
          })
        : await cancelBusinessAction({
            actionId: action.actionId,
            confirmationNonce: secret.confirmationNonce,
            adminToken,
            accessToken: authState.accessToken,
          })

      updateActionUi(messageId, {
        phase: decision === 'confirm' ? 'succeeded' : 'cancelled',
        execution,
        error: null,
        retryDecision: null,
      })
      actionSecretsRef.current.delete(messageId)
      idempotencyKeysRef.current.delete(messageId)
    } catch (requestError) {
      if (requestError instanceof AuthExpiredError || requestError?.httpStatus === 401) {
        clearSession()
        onLogout()
        return
      }
      const safeError = requestError instanceof BusinessActionApiError
        ? requestError
        : new BusinessActionApiError({ message: '业务操作未完成，请稍后重试。' })
      const expired = safeError.errorCode === 'ACTION_EXPIRED'
      const terminal = TERMINAL_ACTION_ERRORS.has(safeError.errorCode)
      const retryable = RETRYABLE_ACTION_ERRORS.has(safeError.errorCode)
        || safeError.httpStatus === 502
        || safeError.httpStatus === 503

      updateActionUi(messageId, {
        phase: expired ? 'expired' : 'error',
        execution: null,
        error: safeError.message,
        retryDecision: !terminal && retryable ? decision : null,
      })

      if (terminal || !retryable) {
        actionSecretsRef.current.delete(messageId)
        idempotencyKeysRef.current.delete(messageId)
      }
    } finally {
      actionLocksRef.current.delete(messageId)
    }
  }

  const handleActionConfirm = messageId => handleActionDecision(messageId, 'confirm')
  const handleActionCancel = messageId => handleActionDecision(messageId, 'cancel')
  const handleActionExpire = messageId => {
    if (actionLocksRef.current.has(messageId)) return
    updateActionUi(messageId, current => {
      if (!current || !['pending', 'error'].includes(current.phase)) return current
      return initialActionUi('expired')
    })
    actionSecretsRef.current.delete(messageId)
    idempotencyKeysRef.current.delete(messageId)
  }

  const handleLogout = () => {
    clearSession()
    onLogout()
  }

  const sendMessage = async () => {
    const question = input.trim()
    if (!question || loading) return

    const requestMode = mode
    setLoading(true)
    setError(null)
    setInput('')

    // 添加用户消息
    setMessages(prev => [...prev, { id: newMessageId(), type: 'user', question }])

    const endpoint = requestMode === 'agent'
      ? '/api/agent/langgraph/chat'
      : '/api/chat'

    const headers = { 'Content-Type': 'application/json', Accept: 'application/json' }
    if (requestMode === 'agent' && adminToken.trim()) {
      headers['X-Admin-Token'] = adminToken.trim()
    }
    // Phase 2: Agent 路由始终携带当前会话的 conversationId（可能没有，
    // 则 Java 服务端生成新的随机 UUID 并通过 X-Conversation-Id 响应头返回）。
    // 普通 RAG (/api/chat) 不参与 conversationId 链路，保持原行为。
    const requestBody = (requestMode === 'agent')
      ? { message: question, conversationId: conversationIdRef.current || undefined }
      : { message: question }
    try {
      const response = await authenticatedFetch(`${JAVA_BASE_URL}${endpoint}`,
        authState.accessToken, {
        method: 'POST',
        headers,
        body: JSON.stringify(requestBody),
      })

      let data = null
      let jsonParseFailed = false
      try {
        data = await response.json()
      } catch {
        jsonParseFailed = true
      }

      const appendAssistantMessage = responseData => {
        const messageId = newMessageId()
        let safeData = responseData
        let actionUi = null

        if (responseData?.pendingAction && typeof responseData.pendingAction === 'object') {
          const { confirmationNonce, ...publicPendingAction } = responseData.pendingAction
          safeData = { ...responseData, pendingAction: publicPendingAction }

          if (isSupportedPendingAction(publicPendingAction)) {
            if (typeof confirmationNonce === 'string' && confirmationNonce.length > 0) {
              actionSecretsRef.current.set(messageId, { confirmationNonce })
              actionUi = initialActionUi()
            } else {
              actionUi = initialActionUi('error', '草稿确认信息不可用，请重新生成草稿。')
            }
          }
        }

        setMessages(prev => [...prev, {
          id: messageId,
          type: 'assistant',
          question,
          result: { question, requestMode, ...safeData },
          resultMode: requestMode,
          actionUi,
        }])
      }

      if (!response.ok) {
        if (data && data.answer) {
          appendAssistantMessage({ httpStatus: response.status, ...data })
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

      // Phase 2: Java 响应头 X-Conversation-Id 是本会话权威 conversationId。
      // 服务端会回传"本次实际使用"的 ID（包括它自动生成的新 UUID），
      // 客户端只需在本地缓存，下一次请求在 body 中带回。
      if (requestMode === 'agent') {
        const headerConversationId = response.headers.get('X-Conversation-Id')
        if (headerConversationId) {
          rememberConversationId(headerConversationId)
        }
      }

      appendAssistantMessage(data)
    } catch (e) {
      if (e instanceof AuthExpiredError || e?.httpStatus === 401) {
        clearSession()
        onLogout()
        return
      }
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
  const lastActionUi = [...messages].reverse().find(m => m.type === 'assistant')?.actionUi

  return (
    <div className="app-layout">
      <Sidebar mode={mode} onModeChange={handleModeChange} loading={loading || actionBusy} />

      <main className="main-area">
        <div className="main-header">
          <div className="header-left">
            {/* 移动端模式切换 - 仅在侧边栏隐藏时显示 */}
            <div className="mobile-mode-switch">
              <select
                className="mode-select"
                value={mode}
                onChange={e => handleModeChange(e.target.value)}
                disabled={loading || actionBusy}
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
          <div className="header-actions">
            <span className="current-user" title={authState.user.username}>
              {authState.user.displayName}
            </span>
            {messages.length > 0 && (
              <button
                className="clear-btn"
                onClick={handleClearMessages}
                disabled={loading || actionBusy}
              >
                清空会话
              </button>
            )}
            <button className="logout-btn" onClick={handleLogout} disabled={loading || actionBusy}>
              退出登录
            </button>
          </div>
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
              {messages.map(msg => (
                msg.type === 'user'
                  ? <UserMessage key={msg.id} question={msg.question} />
                  : <ChatMessage
                      key={msg.id}
                      result={msg.result}
                      resultMode={msg.resultMode}
                      actionUi={msg.actionUi}
                      onActionConfirm={() => handleActionConfirm(msg.id)}
                      onActionCancel={() => handleActionCancel(msg.id)}
                      onActionExpire={() => handleActionExpire(msg.id)}
                    />
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
        actionUi={lastActionUi}
      />
    </div>
  )
}

export default App
