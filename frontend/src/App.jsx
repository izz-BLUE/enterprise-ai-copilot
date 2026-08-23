import { useState, useRef, useEffect } from 'react'
import Sidebar from './components/Sidebar'
import InfoPanel from './components/InfoPanel'
import WelcomeScreen from './components/WelcomeScreen'
import ChatMessage, { UserMessage, LoadingMessage } from './components/ChatMessage'
import ChatInput from './components/ChatInput'
import AdminPanel from './components/AdminPanel'
import AdminLogConsole from './components/AdminLogConsole'
import {
  BusinessActionApiError,
  cancelBusinessAction,
  confirmBusinessAction,
} from './services/businessActionApi'
import { authenticatedFetch, AuthExpiredError } from './services/authApi'
import {
  clearChatHistory,
  isHistoryTerminal,
  isTerminalActionStatus,
  readChatHistory,
  resolveUserIdentity,
  writeChatHistory,
} from './services/chatHistoryStorage'
import './App.css'

const JAVA_BASE_URL = ''  // Vite proxy 转发到 localhost:8080

// 仅当 Java 显式返回 `status` 字段且属于以下白名单时，才视为权威业务终态。
// 其他 errorCode（如 INVALID_CONFIRMATION_NONCE / ACTION_NOT_FOUND / DEMO_IDENTITY_*
// 等）只代表前端 UI 决策，不代表 PendingAction 已进入业务终态，必须由 Java 在
// response.body.status 中显式给出才能同步。
const AUTHORITATIVE_TERMINAL_STATUSES = new Set([
  'SUCCEEDED',
  'CANCELLED',
  'EXPIRED',
  'FAILED',
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
  // 本地聊天历史恢复：同一浏览器、同一账号下，刷新 / 关闭重开 / 退出再登录后
  // 从 localStorage 读取上一次的 conversationId + 聊天记录。
  // - 仅恢复公开字段，confirmationNonce / token 等敏感凭据永不落盘；
  // - 历史 PendingAction 的 UI 状态由 chatHistoryStorage.restoreMessages 恢复：
  //     * 仍处 PENDING_CONFIRMATION 的草稿：nonce 不可恢复，确认 / 取消按钮
  //       禁用并展示"确认凭证已失效，请重新生成申请"；
  //     * SUCCEEDED / CANCELLED / EXPIRED / FAILED：保留用户实际看到的终态 UI，
  //       不得被改成"草稿已过期"。
  // - 若历史最后一条已经是业务终态，conversationId 不再同步到 sessionStorage，
  //   下次发起新业务任务时让服务端分配新会话命名空间。
  // <App /> 在 AuthGate 处按 resolveUserIdentity(user).reactKey 重新挂载，
  // 因此这里的 lazy init 只在挂载时跑一次；后续 user 变化走的是组件 remount。
  // 当前账号的稳定身份与 localStorage key 同步来自同一解析函数，避免不同
  // 账号之间复用 React state。
  const { storageKey: chatHistoryKey } = resolveUserIdentity(authState?.user)
  const [messages, setMessages] = useState(() => {
    if (!chatHistoryKey) return []
    const restored = readChatHistory(chatHistoryKey)
    if (!restored) return []
    return restored.messages
  })
  const [adminToken, setAdminToken] = useState('')
  const [error, setError] = useState(null)
  const [showAdminLogs, setShowAdminLogs] = useState(false)
  const chatEndRef = useRef(null)
  const actionSecretsRef = useRef(new Map())
  const idempotencyKeysRef = useRef(new Map())
  const actionLocksRef = useRef(new Set())
  // Phase 2 conversationId：挂载时同步恢复（sessionStorage 缓存 + localStorage 历史）
  // - 业务终态历史丢弃 conversationId，让服务端分配新会话；
  // - 不是可信身份，仅作为分组 hint 传给 Java。
  const initialConversationId = (() => {
    if (!chatHistoryKey) return readStoredConversationId()
    const restored = readChatHistory(chatHistoryKey)
    if (restored?.conversationId && !isHistoryTerminal(restored.messages)) {
      return restored.conversationId
    }
    return readStoredConversationId()
  })()
  const conversationIdRef = useRef(initialConversationId)

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
    // 退出登录 / 401：仅清空页面内存中的消息、登录态和敏感凭据。
    // 不删除当前账号的 localStorage 聊天历史——重新登录后再恢复。
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

  // 挂载时把从 localStorage 恢复的 conversationId 同步回 sessionStorage，
  // 让"同一 tab 刷新后还在用同一会话命名空间"的行为对齐 Phase 2 设计。
  // 终态历史已被 conversationIdRef init 过滤掉，不会写入 sessionStorage。
  useEffect(() => {
    if (conversationIdRef.current && !sessionStorage.getItem('enterprise-ai-copilot.conversation-id')) {
      writeStoredConversationId(conversationIdRef.current)
    }
    // 仅在挂载时执行一次：conversationIdRef 是 ref，不加入依赖。
  }, [])

  // 新消息 / conversationId 变化后，同步写入当前账号的 localStorage。
  // 仅持久化 conversationId + messages；nonce / token / Idempotency-Key
  // 通过 stripSensitive 在写入前剥离。
  useEffect(() => {
    if (!chatHistoryKey) return
    if (messages.length === 0 && !conversationIdRef.current) return
    writeChatHistory(chatHistoryKey, {
      conversationId: conversationIdRef.current,
      messages,
    })
  }, [chatHistoryKey, messages])

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
    clearConversationId()
    if (chatHistoryKey) clearChatHistory(chatHistoryKey)
  }

  const handleClearMessages = () => {
    if (actionBusy || actionLocksRef.current.size > 0) return
    setMessages([])
    setError(null)
    actionSecretsRef.current.clear()
    idempotencyKeysRef.current.clear()
    actionLocksRef.current.clear()
    clearConversationId()
    if (chatHistoryKey) clearChatHistory(chatHistoryKey)
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

  // 同步 PendingAction.status：仅在 Java 真实响应带来的权威业务终态时调用。
  // - confirm 成功 → 'SUCCEEDED'
  // - cancel 成功 → 'CANCELLED'
  // 不根据本地计时器、LLM 文本或前端推断更新；不在错误路径上调，避免
  // 与 Java `errorCode` 携带的"诊断态"混淆。
  const syncPendingActionStatus = (messageId, status) => {
    if (typeof status !== 'string' || status.length === 0) return
    setMessages(prev => prev.map(message => {
      if (message.id !== messageId) return message
      if (!message.result || typeof message.result !== 'object') return message
      if (!message.result.pendingAction || typeof message.result.pendingAction !== 'object') {
        return message
      }
      return {
        ...message,
        result: {
          ...message.result,
          pendingAction: { ...message.result.pendingAction, status },
        },
      }
    }))
  }

  // 原子 helper：把同一条消息的 actionUi + pendingAction.status 一次性更新，
  // 避免分散调用导致中间态被持久化。
  // 仅接收 Java 权威白名单内的终态字符串；调用方必须先经过 TERMINAL_STATUS_WHITELIST 校验。
  const phaseForTerminalStatus = (status) => {
    switch (status) {
      case 'SUCCEEDED': return 'succeeded'
      case 'CANCELLED': return 'cancelled'
      case 'EXPIRED': return 'expired'
      case 'FAILED': return 'error'
      default: return null
    }
  }

  const applyActionTerminal = (messageId, terminalStatus, opts = {}) => {
    const phase = phaseForTerminalStatus(terminalStatus)
    if (!phase) return
    setMessages(prev => prev.map(message => {
      if (message.id !== messageId) return message
      const safeResult = message.result && typeof message.result === 'object'
        ? { ...message.result }
        : null
      const nextActionUi = {
        phase,
        execution: null,
        error: typeof opts.error === 'string' ? opts.error : null,
        retryDecision: null,
      }
      if (!safeResult || !safeResult.pendingAction || typeof safeResult.pendingAction !== 'object') {
        return { ...message, actionUi: nextActionUi }
      }
      return {
        ...message,
        result: {
          ...safeResult,
          pendingAction: { ...safeResult.pendingAction, status: terminalStatus },
        },
        actionUi: nextActionUi,
      }
    }))
    // 终态生效：丢弃旧 conversationId，让服务端分配新会话命名空间。
    clearConversationId()
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
      // 同步 pendingAction.status：Java 真实响应的权威业务终态。
      // 只有 execution.status 与决策一致时才同步，避免被服务端 error 路径
      // 携带的 PENDING_CONFIRMATION / 诊断态污染。
      const executedStatus = typeof execution?.status === 'string' ? execution.status : null
      if (decision === 'confirm' && executedStatus === 'SUCCEEDED') {
        syncPendingActionStatus(messageId, 'SUCCEEDED')
      } else if (decision === 'cancel' && executedStatus === 'CANCELLED') {
        syncPendingActionStatus(messageId, 'CANCELLED')
      }
      actionSecretsRef.current.delete(messageId)
      idempotencyKeysRef.current.delete(messageId)
      // 业务动作已收口（SUCCEEDED / CANCELLED）：Java 侧已把 conversationId
      // 对应的 memory 转入终态，客户端下一次发起新业务任务前必须丢弃旧
      // conversationId，让服务端生成新的会话命名空间。
      if (decision === 'confirm' || decision === 'cancel') {
        clearConversationId()
      }
    } catch (requestError) {
      if (requestError instanceof AuthExpiredError || requestError?.httpStatus === 401) {
        clearSession()
        onLogout()
        return
      }
      const safeError = requestError instanceof BusinessActionApiError
        ? requestError
        : new BusinessActionApiError({ message: '业务操作未完成，请稍后重试。' })
      const retryable = RETRYABLE_ACTION_ERRORS.has(safeError.errorCode)
        || safeError.httpStatus === 502
        || safeError.httpStatus === 503
      // 错误响应里的 status 字段：是 Java 在 ActionErrorResponse.body.status 中
      // 回传的 PendingAction 真实业务状态（可能为 null / PENDING_CONFIRMATION /
      // 任意终态）。仅当落在 AUTHORITATIVE_TERMINAL_STATUSES 白名单内时才视为
      // 权威业务终态；其他值一律忽略。
      // 此外 errorCode === 'ACTION_EXPIRED' 时 Java 隐含 EXPIRED 终态
      // （参见 BusinessActionService.error → ActionException.actionStatus），
      // 即便 status 字段缺失/为 null 也按 EXPIRED 处理（任务允许的例外）。
      const errorCodeStatus = safeError.errorCode === 'ACTION_EXPIRED' ? 'EXPIRED' : null
      const responseStatus = typeof safeError.status === 'string'
        && AUTHORITATIVE_TERMINAL_STATUSES.has(safeError.status)
        ? safeError.status
        : null
      const terminalStatus = responseStatus || errorCodeStatus

      if (terminalStatus) {
        // 权威业务终态：原子更新 pendingAction.status + actionUi，并丢弃 conversationId
        applyActionTerminal(messageId, terminalStatus, { error: safeError.message })
        actionSecretsRef.current.delete(messageId)
        idempotencyKeysRef.current.delete(messageId)
      } else {
        // 非终态错误（如 INVALID_CONFIRMATION_NONCE / DEMO_IDENTITY_* /
        // ACTION_NOT_FOUND / 网络抖动等）：只更新 UI 显示，不写权威 status，
        // 不清除 conversationId；nonce 仍交给现有 retry 决策控制。
        updateActionUi(messageId, {
          phase: 'error',
          execution: null,
          error: safeError.message,
          retryDecision: retryable ? decision : null,
        })
        if (!retryable) {
          actionSecretsRef.current.delete(messageId)
          idempotencyKeysRef.current.delete(messageId)
        }
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

        // Java 显式返回终态：服务端已把当前 conversationId 对应的 memory
        // 收口（SUCCEEDED / CANCELLED / EXPIRED / FAILED）。下一次发起新
        // 业务任务时不应继续携带旧 conversationId，让服务端分配新会话。
        const returnedStatus = safeData?.pendingAction?.status
        if (typeof returnedStatus === 'string' && isTerminalActionStatus(returnedStatus)) {
          clearConversationId()
        }
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
      <Sidebar
        mode={mode}
        onModeChange={handleModeChange}
        loading={loading || actionBusy}
        userRole={authState?.user?.role}
        onAdminLogsOpen={() => setShowAdminLogs(true)}
        showAdminLogs={showAdminLogs}
      />

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
          {showAdminLogs ? (
            <AdminLogConsole
              accessToken={authState.accessToken}
              onBackToChat={() => setShowAdminLogs(false)}
            />
          ) : messages.length === 0 ? (
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

        {!showAdminLogs && (
          <div className="input-section">
            <AdminPanel adminToken={adminToken} setAdminToken={setAdminToken} />
            <ChatInput
              input={input}
              setInput={setInput}
              onSend={sendMessage}
              loading={loading}
            />
          </div>
        )}
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
