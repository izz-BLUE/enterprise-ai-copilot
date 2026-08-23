// Local chat-history persistence (per authenticated user, browser-only).
//
// 目标：在同一浏览器同一账号下，刷新 / 关闭重开 / 退出后重新登录后，
// 恢复之前的 conversationId 和聊天记录，仅保存在 localStorage，不上传。
//
// 安全：仅持久化公开字段，绝不写入 confirmationNonce / token / nonce /
// Idempotency-Key 等敏感凭据；恢复时也做一次防御性扫描。
//
// 失效保护：localStorage 不可用 / JSON 损坏 / 容量不足 / 账号身份缺失时
// 回退到 null，调用方继续使用纯内存聊天，不阻断主流程。

const STORAGE_PREFIX = 'enterprise-ai-copilot.chat-history.'
const MAX_MESSAGES = 100

// Java PendingAction 的权威业务终态（详见 backend-java ActionStatus）。
// 命中后客户端不再复用当前 conversationId，下次请求会触发服务端生成新的。
export const TERMINAL_ACTION_STATUSES = new Set([
  'SUCCEEDED',
  'FAILED',
  'CANCELLED',
  'EXPIRED',
])

// actionUi 是 PendingActionCard 的 UI 状态机，公开字段白名单。
// execution 子对象只保留恢复"已提交卡片"展示所必需的最小公开字段
// （requestId / replayed），不保存 traceId / originTraceId / completedAt /
// message 等服务端诊断字段，也不保存任何凭证。
const ACTION_UI_PHASES = new Set([
  'pending', 'confirming', 'cancelling',
  'succeeded', 'cancelled', 'expired', 'error',
])

// 持久化前剥离 assistant message 中的敏感字段，避免凭据泄露到 localStorage。
// 即便上游已经在 setMessages 前做过剥离，这里仍做防御性清洗。
const stripSensitive = (message) => {
  if (!message || typeof message !== 'object') return message
  const { type, id, question, result, resultMode, actionUi } = message

  // result.pendingAction 剥离 confirmationNonce；其他公开字段保留
  let safeResult = result && typeof result === 'object' ? { ...result } : result
  if (safeResult && typeof safeResult === 'object' && safeResult.pendingAction
      && typeof safeResult.pendingAction === 'object') {
    const { confirmationNonce, ...publicPendingAction } = safeResult.pendingAction
    void confirmationNonce
    safeResult.pendingAction = publicPendingAction
  }

  // actionUi 只保留白名单字段。execution 子对象只透传公开的
  // requestId / replayed；traceId / originTraceId / completedAt / message 等
  // 服务端诊断字段不入盘；任何未知子字段整体剥除。
  let safeActionUi = null
  if (actionUi && typeof actionUi === 'object') {
    const { phase, error, retryDecision, execution } = actionUi
    let safeExecution = null
    if (execution && typeof execution === 'object') {
      const { requestId, replayed } = execution
      safeExecution = {
        ...(typeof requestId === 'string' && requestId.length > 0 && requestId.length <= 128
          ? { requestId } : {}),
        ...(typeof replayed === 'boolean' ? { replayed } : {}),
      }
    }
    safeActionUi = {
      phase: typeof phase === 'string' && ACTION_UI_PHASES.has(phase) ? phase : 'pending',
      error: typeof error === 'string' ? error : null,
      retryDecision: retryDecision === 'confirm' || retryDecision === 'cancel' ? retryDecision : null,
      ...(safeExecution && Object.keys(safeExecution).length > 0
        ? { execution: safeExecution } : {}),
    }
  }

  return { type, id, question, result: safeResult, resultMode, actionUi: safeActionUi }
}

const sanitizeMessages = (rawMessages) => {
  if (!Array.isArray(rawMessages)) return []
  return rawMessages
    .filter(message => message && typeof message === 'object'
      && (message.type === 'user' || message.type === 'assistant')
      && typeof message.id === 'string')
    .map(stripSensitive)
    .slice(-MAX_MESSAGES)
}

// 唯一稳定身份解析：AuthGate 重挂载 React key 与 localStorage key 必须基于同一解析结果。
// 返回 { storageKey, reactKey, kind }：
//   - storageKey：用于 localStorage；不带则不持久化（避免匿名 key 互相覆盖）；
//   - reactKey：用于 AuthGate 给 <App /> 设 React key；缺失时回退 'anonymous'；
//   - kind：用于诊断 / 测试断言（'userId' / 'employeeId' / 'username' / null）。
// 优先级与历史一致：userId > employeeId > username；不使用 accessToken 作为 key。
export const resolveUserIdentity = (user) => {
  if (!user || typeof user !== 'object') return { storageKey: null, reactKey: 'anonymous', kind: null }
  const candidates = [
    { field: 'userId', value: user.userId },
    { field: 'employeeId', value: user.employeeId },
    { field: 'username', value: user.username },
  ]
  for (const { field, value } of candidates) {
    if (typeof value === 'string' && value.trim().length > 0 && value.length <= 128) {
      return {
        storageKey: STORAGE_PREFIX + value,
        reactKey: value,
        kind: field,
      }
    }
  }
  return { storageKey: null, reactKey: 'anonymous', kind: null }
}

// =============================================================================
// 按 (用户, 模式) 隔离的聊天历史：
//   - 每个模式独立 key：`<storageKey>.<mode>`（如 ...U90001.agent / ...U90001.rag），
//     agent / rag 各自保存、各自恢复，切换时互不覆盖、互不删除；
//   - 旧格式（无模式后缀的 ...U90001）是升级前的单一历史快照，视为默认模式 agent；
//     首次作为 agent 读取时复制到 .agent key（旧 key 保留不删除，不丢数据）；
//   - 上次模式持久化（<storageKey>.last-mode），刷新 / 重开后恢复用户所在模式。
// =============================================================================
export const CHAT_HISTORY_MODES = ['agent', 'rag']
const LAST_MODE_SUFFIX = '.last-mode'

export const isValidChatMode = (mode) => CHAT_HISTORY_MODES.includes(mode)

// 模式白名单校验：拒绝任意后缀，避免注入非法 localStorage key。
export const historyKeyForMode = (baseStorageKey, mode) => {
  if (typeof baseStorageKey !== 'string' || baseStorageKey.length === 0) return null
  if (!isValidChatMode(mode)) return null
  return `${baseStorageKey}.${mode}`
}

// 模式化读取：先读 <storageKey>.<mode>；无数据且为默认模式 agent 时，
// 兼容迁移旧格式（<storageKey> 本身）到 .agent key，并返回迁移结果。
// 迁移为一对一移动：确认写盘成功后才移除旧 key，防止用户"清空会话"后
// 旧格式 key 再次触发迁移造成历史"复活"；写入失败时保留旧 key 不丢数据。
export const readHistoryByMode = (baseStorageKey, mode) => {
  const key = historyKeyForMode(baseStorageKey, mode)
  if (!key) return null
  const scoped = readChatHistory(key)
  if (scoped !== null) return scoped
  if (mode === 'agent') {
    const legacy = readChatHistory(baseStorageKey)
    if (legacy) {
      writeChatHistory(key, legacy)
      if (readChatHistory(key) !== null) {
        clearChatHistory(baseStorageKey)
      }
      return legacy
    }
  }
  return null
}

// 刷新 / 重开后恢复上次所在模式；无记录或值非法时回退 null（调用方使用默认 agent）。
export const readLastMode = (baseStorageKey) => {
  if (typeof baseStorageKey !== 'string' || baseStorageKey.length === 0) return null
  try {
    const raw = localStorage.getItem(baseStorageKey + LAST_MODE_SUFFIX)
    return isValidChatMode(raw) ? raw : null
  } catch {
    return null
  }
}

export const writeLastMode = (baseStorageKey, mode) => {
  if (typeof baseStorageKey !== 'string' || baseStorageKey.length === 0) return
  if (!isValidChatMode(mode)) return
  try {
    localStorage.setItem(baseStorageKey + LAST_MODE_SUFFIX, mode)
  } catch {
    // localStorage 不可用：忽略，缺省回到默认 agent 模式。
  }
}

const safeParse = (raw) => {
  if (typeof raw !== 'string' || raw.length === 0) return null
  try {
    return JSON.parse(raw)
  } catch {
    return null
  }
}

export const isTerminalActionStatus = (status) => TERMINAL_ACTION_STATUSES.has(status)

// 取 message 的 PendingAction 公开 status（剥离了 nonce 之后剩下的）。
const getPendingActionStatus = (message) => {
  const status = message?.result?.pendingAction?.status
  return typeof status === 'string' ? status : null
}

// 判断本地历史中是否已经处于"业务终态"——
// 若最后一条 assistant message 的 pendingAction.status 属于权威终态（SUCCEEDED /
// CANCELLED / EXPIRED / FAILED），恢复时不要把 conversationId 同步到
// sessionStorage，避免新业务任务继续复用旧的会话命名空间。
// 仅依据持久化字段（pendingAction.status），不依赖 actionUi（actionUi 可能被丢弃或重置）。
export const isHistoryTerminal = (messages) => {
  if (!Array.isArray(messages) || messages.length === 0) return false
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const message = messages[i]
    if (!message || message.type !== 'assistant') continue
    const status = getPendingActionStatus(message)
    if (status && TERMINAL_ACTION_STATUSES.has(status)) return true
    return false
  }
  return false
}

// 把持久化后的 PendingAction status 映射回 actionUi.phase，
// 仅在持久化中缺少有效 actionUi 时使用；持久化的 phase 优先（保留用户真实看到的状态）。
const phaseForPendingStatus = (status) => {
  switch (status) {
    case 'SUCCEEDED': return 'succeeded'
    case 'CANCELLED': return 'cancelled'
    case 'EXPIRED': return 'expired'
    case 'FAILED': return 'error'
    default: return null
  }
}

// 公开 API：把从 localStorage 读出的 messages 修复为 App 期望的形状。
// - 用户消息：原样返回；
// - 非 PendingAction assistant：原样返回；
// - 已确认/已取消/已过期/已失败的 PendingAction：复用持久化的 actionUi.phase
//   （保留真实终态 UI），若没有则根据 pendingAction.status 推断；
//   execution 子对象透传 requestId / replayed（公开申请编号），其他字段丢弃；
// - 仍处 PENDING_CONFIRMATION 的 PendingAction：nonce 不可恢复，
//   强制 actionUi = expired 并展示"确认凭证已失效，请重新生成申请"。
//
// 关键不变量：恢复时**永远清除 retryDecision**——nonce 已丢失，不能再触发
// "重试确认 / 重试取消"按钮，否则会向 Java 发送伪 nonce 引发 INVALID_CONFIRMATION_NONCE。
const safeRestoredExecution = (raw) => {
  if (!raw || typeof raw !== 'object') return null
  const { requestId, replayed } = raw
  const safe = {
    ...(typeof requestId === 'string' && requestId.length > 0 && requestId.length <= 128
      ? { requestId } : {}),
    ...(typeof replayed === 'boolean' ? { replayed } : {}),
  }
  return Object.keys(safe).length > 0 ? safe : null
}

export const restoreMessages = (messages) => {
  if (!Array.isArray(messages)) return []
  return messages.map(message => {
    if (!message || message.type !== 'assistant') return message
    const status = getPendingActionStatus(message)
    const hasPendingAction = message.result?.pendingAction
      && typeof message.result.pendingAction === 'object'

    if (!hasPendingAction) return message

    const restoredExecution = safeRestoredExecution(message.actionUi?.execution)

    // 终态：保留用户实际看到的状态 + 公开 execution（申请编号 / 重放标记）
    if (status && TERMINAL_ACTION_STATUSES.has(status)) {
      const existingPhase = message.actionUi?.phase
      const phase = (existingPhase && ACTION_UI_PHASES.has(existingPhase))
        ? existingPhase
        : (phaseForPendingStatus(status) || 'error')
      return {
        ...message,
        actionUi: {
          phase,
          ...(restoredExecution ? { execution: restoredExecution } : {}),
          error: typeof message.actionUi?.error === 'string' ? message.actionUi.error : null,
          retryDecision: null, // 强制清空：无 nonce 不得触发重试按钮
        },
      }
    }

    // 非终态（典型为 PENDING_CONFIRMATION / PROCESSING）：
    // nonce 不可恢复，必须禁用确认 / 取消按钮。
    return {
      ...message,
      actionUi: {
        phase: 'expired',
        execution: null,
        error: '确认凭证已失效，请重新生成申请。',
        retryDecision: null,
      },
    }
  })
}

// 读取历史：返回 { conversationId, messages, updatedAt } 或 null。
// - JSON 损坏 / 字段类型错误 / 消息数组不合法 → 视为无历史；
// - 返回的 messages 已经修复 actionUi，可直接交给 App useState 初始化。
export const readChatHistory = (storageKey) => {
  if (typeof storageKey !== 'string' || storageKey.length === 0) return null
  let raw
  try {
    raw = localStorage.getItem(storageKey)
  } catch {
    return null
  }
  const parsed = safeParse(raw)
  if (!parsed || typeof parsed !== 'object') return null

  const conversationId = typeof parsed.conversationId === 'string'
    && parsed.conversationId.length > 0
    && parsed.conversationId.length <= 64
    ? parsed.conversationId
    : null

  const messages = restoreMessages(sanitizeMessages(parsed.messages))
  const updatedAt = typeof parsed.updatedAt === 'number' && Number.isFinite(parsed.updatedAt)
    ? parsed.updatedAt
    : Date.now()

  return { conversationId, messages, updatedAt }
}

// 写入历史：写入失败（容量、隐私模式、被禁用）静默吞掉，不阻断聊天主流程。
export const writeChatHistory = (storageKey, payload) => {
  if (typeof storageKey !== 'string' || storageKey.length === 0) return
  const sanitizedMessages = sanitizeMessages(payload?.messages)
  const safeConversationId = typeof payload?.conversationId === 'string'
    && payload.conversationId.length > 0
    && payload.conversationId.length <= 64
    ? payload.conversationId
    : null
  const record = {
    conversationId: safeConversationId,
    messages: sanitizedMessages,
    updatedAt: Date.now(),
  }
  try {
    localStorage.setItem(storageKey, JSON.stringify(record))
  } catch {
    // 容量不足 / localStorage 不可用 / 隐私模式：忽略并继续使用内存聊天。
  }
}

// 清除当前账号的历史（用户主动清空聊天时调用）。
export const clearChatHistory = (storageKey) => {
  if (typeof storageKey !== 'string' || storageKey.length === 0) return
  try {
    localStorage.removeItem(storageKey)
  } catch {
    // 忽略
  }
}

export const __TESTING__ = {
  STORAGE_PREFIX,
  MAX_MESSAGES,
  TERMINAL_ACTION_STATUSES,
  ACTION_UI_PHASES,
  stripSensitive,
  sanitizeMessages,
  safeParse,
  isHistoryTerminal,
  restoreMessages,
  getPendingActionStatus,
  phaseForPendingStatus,
  safeRestoredExecution,
  CHAT_HISTORY_MODES,
  LAST_MODE_SUFFIX,
  isValidChatMode,
  historyKeyForMode,
  readHistoryByMode,
  readLastMode,
  writeLastMode,
}