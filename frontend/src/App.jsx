import { useState, useRef, useEffect, useCallback } from 'react'
import Sidebar from './components/Sidebar'
import InfoPanel from './components/InfoPanel'
import WelcomeScreen from './components/WelcomeScreen'
import ChatMessage, { UserMessage, LoadingMessage } from './components/ChatMessage'
import ChatInput from './components/ChatInput'
import AdminPanel from './components/AdminPanel'
import DemoIdentityPanel from './components/DemoIdentityPanel'
import {
  BusinessActionApiError,
  cancelBusinessAction,
  confirmBusinessAction,
} from './services/businessActionApi'
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

function App() {
  const [mode, setMode] = useState('agent')
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [messages, setMessages] = useState([])
  const [adminToken, setAdminToken] = useState('')
  const [demoIdentity, setDemoIdentity] = useState(null)
  const [error, setError] = useState(null)
  const chatEndRef = useRef(null)
  const actionSecretsRef = useRef(new Map())
  const idempotencyKeysRef = useRef(new Map())
  const actionLocksRef = useRef(new Set())

  const actionBusy = messages.some(message =>
    message.actionUi?.phase === 'confirming' || message.actionUi?.phase === 'cancelling')

  const handleInitialIdentity = useCallback(identity => {
    setDemoIdentity(current => current || identity)
  }, [])

  const clearIdentityBoundSession = () => {
    setMessages([])
    setInput('')
    setError(null)
    actionSecretsRef.current.clear()
    idempotencyKeysRef.current.clear()
    actionLocksRef.current.clear()
  }

  const handleDemoIdentityChange = nextIdentity => {
    if (!nextIdentity || nextIdentity.userId === demoIdentity?.userId
        || loading || actionBusy || actionLocksRef.current.size > 0) return
    const hasPendingDraft = messages.some(message =>
      ['pending', 'error'].includes(message.actionUi?.phase))
    if (hasPendingDraft && !window.confirm(
      '切换身份会清空当前会话，当前草稿需要重新生成。')) return
    clearIdentityBoundSession()
    setDemoIdentity(nextIdentity)
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
            demoUserId: demoIdentity?.userId,
          })
        : await cancelBusinessAction({
            actionId: action.actionId,
            confirmationNonce: secret.confirmationNonce,
            adminToken,
            demoUserId: demoIdentity?.userId,
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

  const sendMessage = async () => {
    const question = input.trim()
    if (!question || loading) return

    const requestMode = mode
    if (requestMode === 'agent' && !demoIdentity) {
      setError('请选择有效的演示身份。')
      return
    }
    setLoading(true)
    setError(null)
    setInput('')

    // 添加用户消息
    setMessages(prev => [...prev, { id: newMessageId(), type: 'user', question }])

    const endpoint = requestMode === 'agent'
      ? '/api/agent/langgraph/chat'
      : '/api/chat'

    const headers = { 'Content-Type': 'application/json' }
    if (requestMode === 'agent' && adminToken.trim()) {
      headers['X-Admin-Token'] = adminToken.trim()
    }
    if (requestMode === 'agent') {
      headers['X-Demo-User-Id'] = demoIdentity.userId
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

      appendAssistantMessage(data)
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
          {messages.length > 0 && (
            <button
              className="clear-btn"
              onClick={handleClearMessages}
              disabled={loading || actionBusy}
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
          <DemoIdentityPanel
            demoIdentity={demoIdentity}
            onInitialIdentity={handleInitialIdentity}
            onIdentityChange={handleDemoIdentityChange}
            disabled={loading || actionBusy}
          />
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
