import { useState, useRef, useEffect } from 'react'
import Sidebar from './components/Sidebar'
import InfoPanel from './components/InfoPanel'
import WelcomeScreen from './components/WelcomeScreen'
import ChatMessage, { UserMessage, LoadingMessage } from './components/ChatMessage'
import ChatInput from './components/ChatInput'
import AdminLogConsole from './components/AdminLogConsole'
import MockOaApprovalConsole from './components/MockOaApprovalConsole'
import useBusinessActionFlow from './hooks/useBusinessActionFlow'
import useChatRequest from './hooks/useChatRequest'
import {
  clearChatHistory,
  historyKeyForMode,
  isHistoryTerminal,
  readHistoryByMode,
  readLastMode,
  resolveUserIdentity,
  writeChatHistory,
  writeLastMode,
} from './services/chatHistoryStorage'
import './App.css'

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

function App({ authState, onLogout }) {
  const { storageKey: baseChatHistoryKey } = resolveUserIdentity(authState?.user)
  const [mode, setMode] = useState(() => readLastMode(baseChatHistoryKey) ?? 'agent')
  const [input, setInput] = useState('')
  // 本地聊天历史恢复：同一浏览器、同一账号下，刷新 / 关闭重开 / 退出再登录后
  // 从 localStorage 读取上一次的 conversationId + 聊天记录。
  // - 历史按 (用户, 模式) 隔离：agent / rag 各自独立 key（见 historyKeyForMode），
  //   切换模式时各自保留、各自恢复，互不覆盖；旧格式（无模式后缀）视为默认 agent
  //   并兼容迁移（readHistoryByMode）；
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
  // 账号之间复用 React state。初次渲染时 mode 先于 messages 初始化，
  // 两者使用同一模式读取对应历史快照。
  const chatHistoryKey = historyKeyForMode(baseChatHistoryKey, mode)
  const [messages, setMessages] = useState(() => {
    if (!baseChatHistoryKey) return []
    const restored = readHistoryByMode(baseChatHistoryKey, mode)
    return restored?.messages ?? []
  })
  const [showAdminLogs, setShowAdminLogs] = useState(false)
  const [showMockOa, setShowMockOa] = useState(false)
  const [clearConfirming, setClearConfirming] = useState(false)
  const chatEndRef = useRef(null)
  // Phase 2 conversationId：挂载时按模式恢复（sessionStorage 缓存 + 对应模式历史）
  // - 仅 agent 参与 conversationId 链路；rag（普通 RAG 路由）不携带、不恢复；
  // - 业务终态历史丢弃 conversationId，让服务端分配新会话；
  // - 不是可信身份，仅作为分组 hint 传给 Java。
  const initialConversationId = (() => {
    if (!baseChatHistoryKey) return mode === 'agent' ? readStoredConversationId() : null
    const restored = readHistoryByMode(baseChatHistoryKey, mode)
    if (mode === 'agent' && restored?.conversationId && !isHistoryTerminal(restored.messages)) {
      return restored.conversationId
    }
    return mode === 'agent' ? readStoredConversationId() : null
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

  const {
    actionBusy,
    hasActiveAction,
    resetActionRuntime,
    registerActionSecret,
    handleActionConfirm,
    handleActionCancel,
    handleActionExpire,
  } = useBusinessActionFlow({
    messages,
    setMessages,
    accessToken: authState.accessToken,
    clearConversationId,
    onLogout,
  })

  const handleAuthenticationExpired = () => {
    setMessages([])
    setInput('')
    resetActionRuntime()
    clearConversationId()
    onLogout()
  }

  const {
    loading,
    error,
    failedQuestion,
    sendMessage,
    resetRequestState,
    clearError,
  } = useChatRequest({
    mode,
    input,
    setInput,
    setMessages,
    accessToken: authState.accessToken,
    conversationIdRef,
    rememberConversationId,
    clearConversationId,
    registerActionSecret,
    onAuthenticationExpired: handleAuthenticationExpired,
  })

  const clearSession = () => {
    // 退出登录 / 401：仅清空页面内存中的消息、登录态和敏感凭据。
    // 不删除当前账号的 localStorage 聊天历史——重新登录后再恢复。
    setMessages([])
    setInput('')
    resetRequestState()
    resetActionRuntime()
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
    clearError()
  }

  const handleModeChange = (newMode) => {
    if (hasActiveAction()) return
    // 日志控制台是视图分支而非第三种 mode：在控制台内点击左侧模式按钮时，
    // 必须先退出控制台，再执行模式切换（同模式直接返回，保留聊天状态）。
    if (showAdminLogs || showMockOa) {
      setShowAdminLogs(false)
      setShowMockOa(false)
    }
    if (newMode === mode) {
      return
    }
    // 切换前先把当前模式的最新状态落盘（含 message 数组与 conversationId），
    // 防止最后一次会话变更写丢；随后加载目标模式的独立历史快照。
    // 与保存 effect 同一守卫：无内容（无消息且无 conversationId）不落盘，
    // 避免给从未聊天的用户产生空历史 key。
    if (chatHistoryKey && (messages.length > 0 || conversationIdRef.current)) {
      writeChatHistory(chatHistoryKey, {
        conversationId: conversationIdRef.current,
        messages,
      })
    }
    // 内存凭据边界：confirmationNonce / 幂等键 / 锁按 messageId 与内存消息绑定。
    // 切换模式后目标模式恢复的消息即使 id 相同也绝不能复用旧 nonce 变为可执行
    // （恢复逻辑会强制凭证失效，此处清除是双保险），业务动作仍由 Java 权威校验。
    resetActionRuntime()
    const restored = baseChatHistoryKey
      ? readHistoryByMode(baseChatHistoryKey, newMode)
      : null
    setMode(newMode)
    // 目标模式的历史恢复：无历史则展示空聊天（WelcomeScreen），不阻塞主流程。
    setMessages(restored?.messages ?? [])
    resetRequestState()
    // conversationId 按模式隔离：仅 agent 从自己的历史快照恢复；
    // rag 不参与 conversationId 链路，切到 rag 即清除运行时会话命名空间
    // （agent 的历史快照仍保留在 localStorage，切回时按既有规则恢复）。
    const nextConversationId = newMode === 'agent'
      ? (restored?.conversationId ?? null)
      : null
    conversationIdRef.current = nextConversationId
    writeStoredConversationId(nextConversationId)
    if (baseChatHistoryKey) {
      writeLastMode(baseChatHistoryKey, newMode)
    }
  }

  const handleClearMessages = () => {
    if (hasActiveAction()) return
    if (!clearConfirming) {
      setClearConfirming(true)
      return
    }
    setMessages([])
    resetRequestState()
    resetActionRuntime()
    clearConversationId()
    if (chatHistoryKey) clearChatHistory(chatHistoryKey)
    setClearConfirming(false)
  }

  useEffect(() => {
    if (!clearConfirming) return undefined
    const timer = window.setTimeout(() => setClearConfirming(false), 5000)
    return () => window.clearTimeout(timer)
  }, [clearConfirming])

  const handleLogout = () => {
    clearSession()
    onLogout()
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
        onAdminLogsOpen={() => {
          setShowAdminLogs(true)
          setShowMockOa(false)
        }}
        showAdminLogs={showAdminLogs}
        onMockOaOpen={() => {
          setShowMockOa(true)
          setShowAdminLogs(false)
        }}
        showMockOa={showMockOa}
      />

      <main className={`main-area ${showAdminLogs || showMockOa ? 'admin-view' : ''}`}>
        <div className="main-header">
          <div className="header-left">
            {/* 移动端模式切换 - 仅在侧边栏隐藏时显示 */}
            <div className="mobile-mode-switch">
              <label className="sr-only" htmlFor="mobile-view-select">切换工作区视图</label>
              <select
                id="mobile-view-select"
                className="mode-select"
                value={showAdminLogs ? 'admin-logs' : (showMockOa ? 'mock-oa' : mode)}
                onChange={e => {
                  if (e.target.value === 'admin-logs') {
                    setShowAdminLogs(true)
                    setShowMockOa(false)
                  } else if (e.target.value === 'mock-oa') {
                    setShowMockOa(true)
                    setShowAdminLogs(false)
                  } else {
                    handleModeChange(e.target.value)
                  }
                }}
                disabled={loading || actionBusy}
                aria-label="切换工作区视图"
              >
                <option value="agent">🤖 智能体问答</option>
                <option value="rag">📚 标准问答</option>
                {authState?.user?.role === 'ADMIN' && (
                  <option value="admin-logs">🧾 日志控制台</option>
                )}
                {authState?.user?.role === 'ADMIN' && (
                  <option value="mock-oa">✅ 模拟 OA 审批</option>
                )}
              </select>
            </div>
            <h2 className="header-title">
              {showAdminLogs
                ? '日志控制台'
                : (showMockOa ? '模拟 OA 审批' : (mode === 'agent' ? '智能体问答' : '标准问答'))}
            </h2>
            <span className="header-badge">
              {showAdminLogs
                ? '运行审计'
                : (showMockOa ? '外部审批' : (mode === 'agent' ? '任务协作' : '知识检索'))}
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
                aria-label={clearConfirming ? '确认清空会话' : '清空会话'}
              >
                {clearConfirming ? '确认清空' : '清空会话'}
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
          ) : showMockOa ? (
            <MockOaApprovalConsole
              accessToken={authState.accessToken}
              onBackToChat={() => setShowMockOa(false)}
              onAuthenticationExpired={handleAuthenticationExpired}
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
                  <span className="error-text">{error}</span>
                  {failedQuestion && !loading && (
                    <button type="button" className="error-retry" onClick={() => sendMessage(failedQuestion)}>
                      重新发送
                    </button>
                  )}
                </div>
              )}
              <div ref={chatEndRef} />
            </div>
          )}
        </div>

        {!showAdminLogs && !showMockOa && (
          <InfoPanel
            compact
            result={lastResult}
            resultMode={lastResultMode || mode}
            actionUi={lastActionUi}
          />
        )}

        {!showAdminLogs && !showMockOa && (
          <div className="input-section">
            <ChatInput
              input={input}
              setInput={setInput}
              onSend={sendMessage}
              loading={loading}
            />
          </div>
        )}
      </main>

      {!showAdminLogs && !showMockOa && (
        <InfoPanel
          result={lastResult}
          resultMode={lastResultMode || mode}
          actionUi={lastActionUi}
        />
      )}
    </div>
  )
}

export default App
