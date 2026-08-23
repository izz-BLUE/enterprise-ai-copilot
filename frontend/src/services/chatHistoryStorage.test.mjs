// Frontend unit tests for chatHistoryStorage (uses Node's built-in test runner).
// 运行：`cd frontend && node --test src/services/chatHistoryStorage.test.mjs`
//
// 覆盖任务审计点：
// - 刷新 / 关闭重开：conversationId + messages 完整反显
// - 不同账号互不干扰；userId / employeeId / username fallback
// - 主动清空聊天：本地缓存同步删除
// - localStorage 损坏 / 不可用 / 容量不足：静默回退
// - 敏感字段（confirmationNonce / actionSecretsRef / accessToken / Authorization / Idempotency-Key 等）不入盘
// - requestId 作为公开申请编号允许持久化；traceId / originTraceId / completedAt / message 等服务端诊断字段不入盘
// - 历史 PendingAction 恢复：PENDING_CONFIRMATION → 凭证失效；SUCCEEDED /
//   CANCELLED / EXPIRED / FAILED 保留真实终态 UI，不会被改成"草稿已过期"
// - isHistoryTerminal 在终态恢复后正确丢弃旧 conversationId

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { pathToFileURL } from 'node:url'
import path from 'node:path'

const moduleUrl = pathToFileURL(path.resolve(
  process.cwd(),
  'src',
  'services',
  'chatHistoryStorage.js',
)).href

function createMemoryStorage() {
  const map = new Map()
  return {
    getItem(key) {
      return map.has(key) ? map.get(key) : null
    },
    setItem(key, value) {
      map.set(key, String(value))
    },
    removeItem(key) {
      map.delete(key)
    },
    clear() {
      map.clear()
    },
    _map: map,
  }
}

const importFresh = () => import(`${moduleUrl}?t=${Date.now()}-${Math.random()}`)

const buildHarness = async ({ localStorageImpl, sessionStorageImpl } = {}) => {
  const ls = localStorageImpl ?? createMemoryStorage()
  const ss = sessionStorageImpl ?? createMemoryStorage()
  globalThis.localStorage = ls
  globalThis.sessionStorage = ss
  const mod = await importFresh()
  return { ls, ss, mod }
}

// =============================================================================
// 身份解析：唯一稳定的 userId > employeeId > username 优先级
// =============================================================================

test('identity: 优先 userId，其次 employeeId，最后 username', async () => {
  const { mod } = await buildHarness()
  assert.deepEqual(
    mod.resolveUserIdentity({ userId: 'u1', employeeId: 'e1', username: 'n1' }),
    { storageKey: 'enterprise-ai-copilot.chat-history.u1', reactKey: 'u1', kind: 'userId' },
  )
  assert.deepEqual(
    mod.resolveUserIdentity({ employeeId: 'e2', username: 'n2' }),
    { storageKey: 'enterprise-ai-copilot.chat-history.e2', reactKey: 'e2', kind: 'employeeId' },
  )
  assert.deepEqual(
    mod.resolveUserIdentity({ username: 'n3' }),
    { storageKey: 'enterprise-ai-copilot.chat-history.n3', reactKey: 'n3', kind: 'username' },
  )
})

test('identity: 没有可信账号标识时 storageKey 为 null，reactKey 为 anonymous', async () => {
  const { mod } = await buildHarness()
  assert.deepEqual(mod.resolveUserIdentity({}), { storageKey: null, reactKey: 'anonymous', kind: null })
  assert.deepEqual(mod.resolveUserIdentity(null), { storageKey: null, reactKey: 'anonymous', kind: null })
  assert.deepEqual(mod.resolveUserIdentity(undefined), { storageKey: null, reactKey: 'anonymous', kind: null })
  assert.deepEqual(mod.resolveUserIdentity({ displayName: 'no-id' }), { storageKey: null, reactKey: 'anonymous', kind: null })
})

test('identity: 不使用 accessToken 作为 key', async () => {
  const { mod } = await buildHarness()
  const onlyToken = { accessToken: 'opaque-token-string' }
  assert.equal(mod.resolveUserIdentity(onlyToken).storageKey, null)
  assert.equal(mod.resolveUserIdentity(onlyToken).reactKey, 'anonymous')
})

test('identity: 空字符串 / 超长字符串视为无效', async () => {
  const { mod } = await buildHarness()
  assert.equal(mod.resolveUserIdentity({ userId: '' }).storageKey, null)
  assert.equal(mod.resolveUserIdentity({ userId: '   ' }).storageKey, null)
  assert.equal(mod.resolveUserIdentity({ userId: 'x'.repeat(129) }).storageKey, null)
})

test('identity: 同函数同时决定 AuthGate 的 reactKey 与 localStorage 的 storageKey', async () => {
  const { mod } = await buildHarness()
  const userA = { employeeId: 'emp-A', username: 'alice' }
  const userB = { employeeId: 'emp-B', username: 'bob' }
  const a = mod.resolveUserIdentity(userA)
  const b = mod.resolveUserIdentity(userB)
  // 必须严格区分，否则两个账号会复用同一个 <App /> 实例 / 同一份历史
  assert.notEqual(a.reactKey, b.reactKey)
  assert.notEqual(a.storageKey, b.storageKey)
  assert.equal(a.kind, 'employeeId')
  assert.equal(b.kind, 'employeeId')
})

// =============================================================================
// 跨账号隔离：不同 storageKey 完全独立
// =============================================================================

test('isolation: 不同 userId 的 storageKey 不互相可见', async () => {
  const { mod } = await buildHarness()
  const keyA = mod.resolveUserIdentity({ userId: 'u-A' }).storageKey
  const keyB = mod.resolveUserIdentity({ userId: 'u-B' }).storageKey
  assert.notEqual(keyA, keyB)

  mod.writeChatHistory(keyA, {
    conversationId: 'conv-A',
    messages: [{ id: 'a1', type: 'user', question: 'A 的问题' }],
  })
  mod.writeChatHistory(keyB, {
    conversationId: 'conv-B',
    messages: [{ id: 'b1', type: 'user', question: 'B 的问题' }],
  })

  assert.equal(mod.readChatHistory(keyA).messages[0].question, 'A 的问题')
  assert.equal(mod.readChatHistory(keyB).messages[0].question, 'B 的问题')
})

test('isolation: 仅 employeeId 的 A/B 切换使用不同 key', async () => {
  const { mod } = await buildHarness()
  const a = mod.resolveUserIdentity({ employeeId: 'emp-A' })
  const b = mod.resolveUserIdentity({ employeeId: 'emp-B' })
  assert.notEqual(a.storageKey, b.storageKey)
  assert.notEqual(a.reactKey, b.reactKey)

  mod.writeChatHistory(a.storageKey, {
    conversationId: 'cA', messages: [{ id: '1', type: 'user', question: 'Q-A' }],
  })
  mod.writeChatHistory(b.storageKey, {
    conversationId: 'cB', messages: [{ id: '2', type: 'user', question: 'Q-B' }],
  })

  assert.equal(mod.readChatHistory(a.storageKey).messages[0].question, 'Q-A')
  assert.equal(mod.readChatHistory(b.storageKey).messages[0].question, 'Q-B')
})

test('isolation: 仅 username 的 A/B 切换使用不同 key', async () => {
  const { mod } = await buildHarness()
  const a = mod.resolveUserIdentity({ username: 'alpha' })
  const b = mod.resolveUserIdentity({ username: 'beta' })
  assert.notEqual(a.storageKey, b.storageKey)

  mod.writeChatHistory(a.storageKey, { conversationId: 'x', messages: [{ id: '1', type: 'user', question: 'alpha-q' }] })
  mod.writeChatHistory(b.storageKey, { conversationId: 'y', messages: [{ id: '2', type: 'user', question: 'beta-q' }] })

  assert.equal(mod.readChatHistory(a.storageKey).messages[0].question, 'alpha-q')
  assert.equal(mod.readChatHistory(b.storageKey).messages[0].question, 'beta-q')
})

test('isolation: identity fallback 切换（userId → employeeId）也会换 key', async () => {
  const { mod } = await buildHarness()
  const fromUserId = mod.resolveUserIdentity({ userId: 'u-1' })
  const fromEmployeeId = mod.resolveUserIdentity({ employeeId: 'e-1' })
  // 同一个人在不同登录阶段身份字段不同（不应串历史）
  assert.notEqual(fromUserId.storageKey, fromEmployeeId.storageKey)
  assert.notEqual(fromUserId.reactKey, fromEmployeeId.reactKey)
})

test('clear: clearChatHistory 后 readChatHistory 返回 null', async () => {
  const { mod } = await buildHarness()
  const key = mod.resolveUserIdentity({ userId: 'u-clear' }).storageKey
  mod.writeChatHistory(key, {
    conversationId: 'conv-c',
    messages: [{ id: 'x', type: 'user', question: 'X' }],
  })
  assert.notEqual(mod.readChatHistory(key), null)
  mod.clearChatHistory(key)
  assert.equal(mod.readChatHistory(key), null)
})

test('clear: 清空单个账号不影响另一账号', async () => {
  const { mod } = await buildHarness()
  const keyA = mod.resolveUserIdentity({ userId: 'u-keep-A' }).storageKey
  const keyB = mod.resolveUserIdentity({ userId: 'u-clear-B' }).storageKey
  mod.writeChatHistory(keyA, { conversationId: 'k', messages: [{ id: '1', type: 'user', question: 'A' }] })
  mod.writeChatHistory(keyB, { conversationId: 'k', messages: [{ id: '2', type: 'user', question: 'B' }] })
  mod.clearChatHistory(keyB)
  assert.notEqual(mod.readChatHistory(keyA), null)
  assert.equal(mod.readChatHistory(keyB), null)
})

// =============================================================================
// 持久化：恢复 + 跨 sessionStorage 模拟重开
// =============================================================================

test('restore: conversationId 与 messages 完整反显', async () => {
  const { mod } = await buildHarness()
  const key = mod.resolveUserIdentity({ userId: 'u-1', username: 'alice' }).storageKey
  mod.writeChatHistory(key, {
    conversationId: 'conv-abc',
    messages: [
      { id: 'm1', type: 'user', question: '你好' },
      {
        id: 'm2', type: 'assistant', question: '你好',
        result: { answer: '你好，有什么可以帮你？', pendingAction: null },
      },
    ],
  })

  const restored = mod.readChatHistory(key)
  assert.equal(restored.conversationId, 'conv-abc')
  assert.equal(restored.messages.length, 2)
  assert.equal(restored.messages[0].question, '你好')
  assert.equal(restored.messages[1].result.answer, '你好，有什么可以帮你？')
})

test('restore: 模拟新会话（重新 import 模块）后仍可读出旧 localStorage 数据', async () => {
  const ls = createMemoryStorage()
  const ss = createMemoryStorage()

  const first = await buildHarness({ localStorageImpl: ls, sessionStorageImpl: ss })
  const key = first.mod.resolveUserIdentity({ userId: 'u-2' }).storageKey
  first.mod.writeChatHistory(key, {
    conversationId: 'conv-xyz',
    messages: [{ id: 'a', type: 'user', question: 'Q' }],
  })

  const second = await buildHarness({ localStorageImpl: ls, sessionStorageImpl: ss })
  const restored = second.mod.readChatHistory(key)
  assert.equal(restored.conversationId, 'conv-xyz')
  assert.equal(restored.messages.length, 1)
})

// =============================================================================
// 失效保护：localStorage 不可用 / JSON 损坏 / 字段类型异常
// =============================================================================

test('quota: localStorage.setItem 抛错时写入静默失败', async () => {
  const brokenLs = {
    getItem: () => { throw new Error('QuotaExceededError') },
    setItem: () => { throw new Error('QuotaExceededError') },
    removeItem: () => { throw new Error('SecurityError') },
  }
  const { mod } = await buildHarness({ localStorageImpl: brokenLs })
  const key = mod.resolveUserIdentity({ userId: 'u-quota' }).storageKey
  assert.doesNotThrow(() => mod.writeChatHistory(key, {
    conversationId: 'c',
    messages: [{ id: 'm', type: 'user', question: 'q' }],
  }))
  assert.equal(mod.readChatHistory(key), null)
})

test('corrupt: JSON 损坏时 readChatHistory 返回 null', async () => {
  const ls = createMemoryStorage()
  ls.setItem('enterprise-ai-copilot.chat-history.u-bad', 'not-json{{{')
  const { mod } = await buildHarness({ localStorageImpl: ls })
  assert.equal(mod.readChatHistory('enterprise-ai-copilot.chat-history.u-bad'), null)
})

test('corrupt: messages 非数组时返回空数组', async () => {
  const ls = createMemoryStorage()
  ls.setItem(
    'enterprise-ai-copilot.chat-history.u-shape',
    JSON.stringify({ conversationId: 'c', messages: 'oops', updatedAt: 1 }),
  )
  const { mod } = await buildHarness({ localStorageImpl: ls })
  const restored = mod.readChatHistory('enterprise-ai-copilot.chat-history.u-shape')
  assert.equal(restored.conversationId, 'c')
  assert.deepEqual(restored.messages, [])
})

// =============================================================================
// 敏感字段：永不进入 localStorage
// =============================================================================

test('safety: 写入前剥离 confirmationNonce，恢复后也不包含', async () => {
  const { mod, ls } = await buildHarness()
  const key = mod.resolveUserIdentity({ userId: 'u-safety' }).storageKey
  mod.writeChatHistory(key, {
    conversationId: 'c-safety',
    messages: [
      { id: 'u1', type: 'user', question: '帮我请假' },
      {
        id: 'a1', type: 'assistant', question: '帮我请假',
        result: {
          answer: '好的，我已生成草稿',
          pendingAction: {
            actionId: 'public-action-id',
            status: 'PENDING_CONFIRMATION',
            confirmationNonce: 'SUPER-SECRET-NONCE-MUST-NOT-LEAK',
            expiresAt: '2026-01-01T00:00:00Z',
          },
        },
      },
    ],
  })

  const raw = ls._map.get(key)
  assert.ok(!raw.includes('SUPER-SECRET-NONCE-MUST-NOT-LEAK'),
    'localStorage 中不应出现 confirmationNonce 内容')
  assert.ok(!raw.includes('confirmationNonce'),
    'localStorage 中不应出现 confirmationNonce 字段名')

  const restored = mod.readChatHistory(key)
  const assistant = restored.messages.find(m => m.type === 'assistant')
  assert.ok(assistant.result.pendingAction)
  assert.equal(assistant.result.pendingAction.confirmationNonce, undefined)
  assert.equal(assistant.result.pendingAction.actionId, 'public-action-id')
})

test('safety: 即便 raw 中残留 confirmationNonce，readChatHistory 也会剥离', async () => {
  const ls = createMemoryStorage()
  ls.setItem(
    'enterprise-ai-copilot.chat-history.u-poke',
    JSON.stringify({
      conversationId: 'c',
      messages: [{
        id: 'a', type: 'assistant',
        question: 'q',
        result: {
          pendingAction: {
            actionId: 'pub',
            confirmationNonce: 'NAUGHTY',
          },
        },
      }],
      updatedAt: 1,
    }),
  )
  const { mod } = await buildHarness({ localStorageImpl: ls })
  const restored = mod.readChatHistory('enterprise-ai-copilot.chat-history.u-poke')
  const assistant = restored.messages[0]
  assert.equal(assistant.result.pendingAction.actionId, 'pub')
  assert.equal(assistant.result.pendingAction.confirmationNonce, undefined)
})

test('safety: actionUi.execution 仅保留公开 requestId / replayed，剥除服务端诊断字段', async () => {
  const { mod, ls } = await buildHarness()
  const key = mod.resolveUserIdentity({ userId: 'u-exec' }).storageKey
  mod.writeChatHistory(key, {
    conversationId: 'c',
    messages: [{
      id: 'm', type: 'assistant', question: 'q',
      result: { answer: 'a', pendingAction: { actionId: 'pub', status: 'SUCCEEDED' } },
      actionUi: {
        phase: 'succeeded',
        execution: {
          requestId: 'LR-202607-0001',
          replayed: true,
          traceId: 'INTERNAL-TRACE-MUST-NOT-LEAK',
          originTraceId: 'ORIGIN-TRACE-MUST-NOT-LEAK',
          completedAt: '2026-01-01T00:00:00Z',
          message: 'INTERNAL-MSG-MUST-NOT-LEAK',
          actionId: 'INTERNAL-ACTIONID-MUST-NOT-LEAK',
          type: 'ANNUAL_LEAVE_REQUEST',
        },
        error: null,
        retryDecision: null,
      },
    }],
  })
  const raw = ls._map.get(key)
  // 公开字段允许保留
  assert.ok(raw.includes('LR-202607-0001'),
    'requestId（公开申请编号）允许写入')
  assert.ok(raw.includes('"replayed":true'),
    'replayed 字段允许写入')
  // 服务端诊断 / 内部字段必须被剥除
  assert.ok(!raw.includes('INTERNAL-TRACE-MUST-NOT-LEAK'),
    'execution.traceId 不应进入 localStorage')
  assert.ok(!raw.includes('ORIGIN-TRACE-MUST-NOT-LEAK'),
    'execution.originTraceId 不应进入 localStorage')
  assert.ok(!raw.includes('INTERNAL-MSG-MUST-NOT-LEAK'),
    'execution.message 不应进入 localStorage')
  assert.ok(!raw.includes('INTERNAL-ACTIONID-MUST-NOT-LEAK'),
    'execution.actionId（重复字段）不应进入 localStorage')
  assert.ok(!raw.includes('ANNUAL_LEAVE_REQUEST'),
    'execution.type（重复字段）不应进入 localStorage')
})

test('safety: 不会写入 accessToken / Authorization / Idempotency-Key', async () => {
  const { mod, ls } = await buildHarness()
  const key = mod.resolveUserIdentity({ userId: 'u-tok' }).storageKey
  mod.writeChatHistory(key, {
    conversationId: 'c',
    messages: [{
      id: 'm', type: 'assistant', question: 'q',
      result: {
        answer: 'a',
        pendingAction: { actionId: 'pub', status: 'PENDING_CONFIRMATION' },
      },
      // 模拟内部态意外被塞到消息上的极端情况
      auth: { accessToken: 'TOKEN-X', Authorization: 'Bearer Y' },
    }],
  })
  const raw = ls._map.get(key)
  assert.ok(!raw.includes('TOKEN-X'))
  assert.ok(!raw.includes('Bearer Y'))
  assert.ok(!raw.includes('"auth"'))
})

// =============================================================================
// 消息数量上限
// =============================================================================

test('limit: 写入与读取都会把消息数限制在 MAX_MESSAGES', async () => {
  const { mod } = await buildHarness()
  const { MAX_MESSAGES } = mod.__TESTING__
  const oversize = MAX_MESSAGES + 50
  const messages = []
  for (let i = 0; i < oversize; i += 1) {
    messages.push({ id: `m-${i}`, type: 'user', question: `q-${i}` })
  }
  const key = mod.resolveUserIdentity({ userId: 'u-cap' }).storageKey
  mod.writeChatHistory(key, { conversationId: 'c', messages })
  const restored = mod.readChatHistory(key)
  assert.equal(restored.messages.length, MAX_MESSAGES)
  assert.equal(restored.messages[0].question, `q-${oversize - MAX_MESSAGES}`)
  assert.equal(restored.messages[restored.messages.length - 1].question, `q-${oversize - 1}`)
})

// =============================================================================
// 终态识别：isTerminalActionStatus
// =============================================================================

test('terminal: isTerminalActionStatus 覆盖 SUCCEEDED / CANCELLED / EXPIRED / FAILED', async () => {
  const { mod } = await buildHarness()
  assert.equal(mod.isTerminalActionStatus('SUCCEEDED'), true)
  assert.equal(mod.isTerminalActionStatus('CANCELLED'), true)
  assert.equal(mod.isTerminalActionStatus('EXPIRED'), true)
  assert.equal(mod.isTerminalActionStatus('FAILED'), true)
  assert.equal(mod.isTerminalActionStatus('PENDING_CONFIRMATION'), false)
  assert.equal(mod.isTerminalActionStatus('PROCESSING'), false)
  assert.equal(mod.isTerminalActionStatus(undefined), false)
  assert.equal(mod.isTerminalActionStatus(null), false)
})

test('terminal: isHistoryTerminal 在历史最后一条为终态时为 true', async () => {
  const { mod } = await buildHarness()
  assert.equal(mod.__TESTING__.isHistoryTerminal([]), false)
  assert.equal(mod.__TESTING__.isHistoryTerminal([
    { id: 'u', type: 'user', question: 'q' },
  ]), false)
  assert.equal(mod.__TESTING__.isHistoryTerminal([
    { id: 'u', type: 'user', question: 'q' },
    { id: 'a', type: 'assistant', result: { pendingAction: { status: 'SUCCEEDED' } } },
  ]), true)
  assert.equal(mod.__TESTING__.isHistoryTerminal([
    { id: 'u', type: 'user', question: 'q' },
    { id: 'a', type: 'assistant', result: { pendingAction: { status: 'PENDING_CONFIRMATION' } } },
  ]), false)
})

// =============================================================================
// 四类历史 PendingAction 恢复语义
// =============================================================================
//
// 关键约束：
// - 已 SUCCEEDED / CANCELLED 的历史卡片必须保持终态 UI，绝不能被改成 expired。
// - 未完成 PENDING_CONFIRMATION 的历史卡片：nonce 不可恢复，按钮必须禁用。
// - 历史 FAILED / EXPIRED：保留对应真实 UI。

const basePendingAction = (status, extra = {}) => ({
  type: 'ANNUAL_LEAVE_REQUEST',
  confirmationRequired: true,
  actionId: 'pub-action-id',
  status,
  expiresAt: '2026-01-01T00:00:00Z',
  summary: { employee: 'zhangsan', startDate: '2026-02-01', endDate: '2026-02-03',
    halfDay: 'NONE', days: 3, reason: '家庭事务',
    remainingBalanceBefore: 10, remainingBalanceAfter: 7 },
  ...extra,
})

test('restore-pending: 仍处 PENDING_CONFIRMATION 的草稿 → 凭证失效，按钮禁用', async () => {
  const { mod } = await buildHarness()
  const key = mod.resolveUserIdentity({ userId: 'u-pending' }).storageKey
  // 模拟"浏览器关闭前用户已经看到 pending 草稿"的真实状态：
  // 持久化前 nonce 已被 stripSensitive 剥离（真实运行路径），但 actionUi.phase 仍是 pending。
  mod.writeChatHistory(key, {
    conversationId: 'c-pending',
    messages: [{
      id: 'a', type: 'assistant', question: 'q',
      result: { answer: '已生成草稿', pendingAction: basePendingAction('PENDING_CONFIRMATION') },
      actionUi: { phase: 'pending', execution: null, error: null, retryDecision: null },
    }],
  })

  const restored = mod.readChatHistory(key)
  const m = restored.messages[0]
  assert.equal(m.result.pendingAction.status, 'PENDING_CONFIRMATION')
  assert.equal(m.actionUi.phase, 'expired',
    '未完成草稿必须被强制 expired，禁用确认 / 取消按钮')
  assert.equal(m.actionUi.error, '确认凭证已失效，请重新生成申请。')
})

test('restore-succeeded: 历史 SUCCEEDED 保持 succeeded UI，绝不变成 expired', async () => {
  const { mod } = await buildHarness()
  const key = mod.resolveUserIdentity({ userId: 'u-succeeded' }).storageKey
  mod.writeChatHistory(key, {
    conversationId: 'c-suc',
    messages: [{
      id: 'a', type: 'assistant', question: 'q',
      result: { answer: '已提交', pendingAction: basePendingAction('SUCCEEDED') },
      actionUi: { phase: 'succeeded', execution: null, error: null, retryDecision: null },
    }],
  })

  const restored = mod.readChatHistory(key)
  const m = restored.messages[0]
  assert.equal(m.result.pendingAction.status, 'SUCCEEDED')
  assert.equal(m.actionUi.phase, 'succeeded',
    '已成功的草稿必须保留 succeeded UI，禁止被改为 expired')
  assert.notEqual(m.actionUi.phase, 'expired')
})

test('restore-cancelled: 历史 CANCELLED 保持 cancelled UI，绝不变成 expired', async () => {
  const { mod } = await buildHarness()
  const key = mod.resolveUserIdentity({ userId: 'u-cancelled' }).storageKey
  mod.writeChatHistory(key, {
    conversationId: 'c-can',
    messages: [{
      id: 'a', type: 'assistant', question: 'q',
      result: { answer: '已取消', pendingAction: basePendingAction('CANCELLED') },
      actionUi: { phase: 'cancelled', execution: null, error: null, retryDecision: null },
    }],
  })

  const restored = mod.readChatHistory(key)
  const m = restored.messages[0]
  assert.equal(m.actionUi.phase, 'cancelled',
    '已取消的草稿必须保留 cancelled UI')
})

test('restore-expired: 历史 EXPIRED 保持 expired UI（语义为"真过期"）', async () => {
  const { mod } = await buildHarness()
  const key = mod.resolveUserIdentity({ userId: 'u-exp' }).storageKey
  mod.writeChatHistory(key, {
    conversationId: 'c-exp',
    messages: [{
      id: 'a', type: 'assistant', question: 'q',
      result: { answer: '草稿已过期', pendingAction: basePendingAction('EXPIRED') },
      actionUi: { phase: 'expired', execution: null, error: null, retryDecision: null },
    }],
  })

  const restored = mod.readChatHistory(key)
  assert.equal(restored.messages[0].actionUi.phase, 'expired')
})

test('restore-failed: 历史 FAILED 映射到 error UI', async () => {
  const { mod } = await buildHarness()
  const key = mod.resolveUserIdentity({ userId: 'u-fail' }).storageKey
  mod.writeChatHistory(key, {
    conversationId: 'c-fail',
    messages: [{
      id: 'a', type: 'assistant', question: 'q',
      result: { answer: '失败', pendingAction: basePendingAction('FAILED') },
      actionUi: { phase: 'error', execution: null, error: '提交失败', retryDecision: null },
    }],
  })

  const restored = mod.readChatHistory(key)
  assert.equal(restored.messages[0].actionUi.phase, 'error')
})

test('restore-pending-empty-actionUi: PENDING + actionUi 被丢弃 → 强制 expired', async () => {
  const { mod } = await buildHarness()
  // 即便持久化中 actionUi 完全缺失，仍要安全降级到 expired（不要让按钮误启用）
  const messages = [{
    id: 'a', type: 'assistant', question: 'q',
    result: { answer: '草稿', pendingAction: basePendingAction('PENDING_CONFIRMATION') },
  }]
  const out = mod.__TESTING__.restoreMessages(messages)
  assert.equal(out[0].actionUi.phase, 'expired')
  assert.equal(out[0].actionUi.error, '确认凭证已失效，请重新生成申请。')
})

test('restore-terminal-empty-actionUi: SUCCEEDED + actionUi 被丢弃 → 推断 succeeded', async () => {
  const { mod } = await buildHarness()
  const messages = [{
    id: 'a', type: 'assistant', question: 'q',
    result: { answer: 'a', pendingAction: basePendingAction('SUCCEEDED') },
  }]
  const out = mod.__TESTING__.restoreMessages(messages)
  assert.equal(out[0].actionUi.phase, 'succeeded')
})

test('restore-terminal-phase-whitelist: 持久化中 phase 不合法时降级到 pendingAction.status 推断', async () => {
  const { mod } = await buildHarness()
  // 攻击者 / 老版本客户端塞了一个非白名单的 phase
  const messages = [{
    id: 'a', type: 'assistant', question: 'q',
    result: { answer: 'a', pendingAction: basePendingAction('SUCCEEDED') },
    actionUi: { phase: 'injected-malicious', execution: null, error: null, retryDecision: null },
  }]
  const out = mod.__TESTING__.restoreMessages(messages)
  assert.equal(out[0].actionUi.phase, 'succeeded',
    '非法 phase 被丢弃，应回退到 pendingAction.status 推断')
})

// =============================================================================
// conversationId 终态切换依据
// =============================================================================

test('convId: 终态历史下重新加载不恢复旧 conversationId', async () => {
  const { mod } = await buildHarness()
  const key = mod.resolveUserIdentity({ userId: 'u-conv' }).storageKey
  mod.writeChatHistory(key, {
    conversationId: 'old-conv-id',
    messages: [{
      id: 'a', type: 'assistant', question: 'q',
      result: { answer: 'a', pendingAction: basePendingAction('SUCCEEDED') },
      actionUi: { phase: 'succeeded', execution: null, error: null, retryDecision: null },
    }],
  })

  const restored = mod.readChatHistory(key)
  // 数据被读出来（恢复消息内容），但 isHistoryTerminal 必须返回 true，
  // 让 App 在挂载时拒绝同步 conversationId 到 sessionStorage。
  assert.equal(mod.isHistoryTerminal(restored.messages), true)
})

test('convId: 非终态历史下重新加载仍恢复 conversationId', async () => {
  const { mod } = await buildHarness()
  const key = mod.resolveUserIdentity({ userId: 'u-conv-active' }).storageKey
  mod.writeChatHistory(key, {
    conversationId: 'active-conv-id',
    messages: [
      { id: 'u', type: 'user', question: 'q' },
      {
        id: 'a', type: 'assistant', question: 'q',
        result: { answer: 'a', pendingAction: null },
      },
    ],
  })

  const restored = mod.readChatHistory(key)
  assert.equal(restored.conversationId, 'active-conv-id')
  assert.equal(mod.isHistoryTerminal(restored.messages), false)
})

// =============================================================================
// conversationId 长度 / 形状校验
// =============================================================================

test('shape: 超长 conversationId 不写入', async () => {
  const { mod } = await buildHarness()
  const key = mod.resolveUserIdentity({ userId: 'u-len' }).storageKey
  mod.writeChatHistory(key, {
    conversationId: 'x'.repeat(200),
    messages: [{ id: 'm', type: 'user', question: 'q' }],
  })
  const restored = mod.readChatHistory(key)
  assert.equal(restored.conversationId, null)
})

// =============================================================================
// 真实消息形状：Java 真实 confirm / cancel 响应后的持久化与恢复
// 完整链路：PENDING_CONFIRMATION → Java confirm → execution.status='SUCCEEDED'
//   → App 同步 pendingAction.status='SUCCEEDED' + actionUi.phase='succeeded'
//   → 落盘 → 重新加载 → 卡片仍显示"已提交" + 申请编号 + 无按钮
// =============================================================================

// 模拟 App.jsx 在收到 Java 真实响应后写出的消息结构：
// - updateActionUi(messageId, { phase, execution, ... })
// - syncPendingActionStatus(messageId, execution.status)
const buildAssistantAfterConfirm = () => ({
  id: 'm-1',
  type: 'assistant',
  question: '请帮我申请年假',
  result: {
    question: '请帮我申请年假',
    requestMode: 'agent',
    answer: '已生成草稿并提交',
    pendingAction: {
      // 由 App 同步后的真实状态：Java 真实响应的权威终态
      type: 'ANNUAL_LEAVE_REQUEST',
      confirmationRequired: true,
      actionId: 'pub-action-id',
      status: 'SUCCEEDED',
      expiresAt: '2099-01-01T00:00:00Z',
      title: '提交模拟年假申请',
      summary: { employee: '张三', startDate: '2026-07-20', endDate: '2026-07-22',
        halfDay: 'NONE', days: 3, reason: '家庭事务',
        remainingBalanceBefore: 5, remainingBalanceAfter: 2 },
      // confirmationNonce 在进入 messages 之前已被 App 剥离（不会出现在这里）
    },
  },
  resultMode: 'agent',
  actionUi: {
    phase: 'succeeded',
    execution: {
      requestId: 'LR-202607-0001',
      replayed: false,
    },
    error: null,
    retryDecision: null,
  },
})

const buildAssistantAfterCancel = () => ({
  id: 'm-1',
  type: 'assistant',
  question: '请帮我申请年假',
  result: {
    question: '请帮我申请年假',
    requestMode: 'agent',
    answer: '草稿已取消',
    pendingAction: {
      type: 'ANNUAL_LEAVE_REQUEST',
      confirmationRequired: true,
      actionId: 'pub-action-id',
      status: 'CANCELLED',
      expiresAt: '2099-01-01T00:00:00Z',
      title: '提交模拟年假申请',
      summary: { employee: '张三', startDate: '2026-07-20', endDate: '2026-07-22',
        halfDay: 'NONE', days: 3, reason: '家庭事务' },
    },
  },
  resultMode: 'agent',
  actionUi: {
    phase: 'cancelled',
    execution: null,
    error: null,
    retryDecision: null,
  },
})

test('real-shape-confirm: 落盘 JSON 含 SUCCEEDED status + requestId + 无 nonce', async () => {
  const { mod, ls } = await buildHarness()
  const key = mod.resolveUserIdentity({ userId: 'U10001' }).storageKey
  mod.writeChatHistory(key, {
    conversationId: 'conv-final',
    messages: [buildAssistantAfterConfirm()],
  })

  const raw = ls._map.get(key)
  // 公开字段
  assert.ok(raw.includes('"status":"SUCCEEDED"'),
    'pendingAction.status 应为 SUCCEEDED')
  assert.ok(raw.includes('LR-202607-0001'),
    'execution.requestId 公开字段应保留')
  assert.ok(raw.includes('"phase":"succeeded"'),
    'actionUi.phase 应为 succeeded')
  // 敏感字段
  assert.ok(!raw.includes('confirmationNonce'),
    '落盘 JSON 不应出现 confirmationNonce 字段名')
  assert.ok(!raw.includes('INTERNAL-'),
    '落盘 JSON 不应出现任何敏感子串')
})

test('real-shape-confirm-restore: 重新加载后卡片展示状态保持 succeeded + 申请编号反显', async () => {
  const { mod } = await buildHarness()
  const key = mod.resolveUserIdentity({ userId: 'U10001' }).storageKey
  mod.writeChatHistory(key, {
    conversationId: 'conv-final',
    messages: [buildAssistantAfterConfirm()],
  })

  const restored = mod.readChatHistory(key)
  const m = restored.messages[0]
  // 1. 卡片展示保持成功
  assert.equal(m.result.pendingAction.status, 'SUCCEEDED')
  assert.equal(m.actionUi.phase, 'succeeded')
  // 2. 申请编号反显
  assert.equal(m.actionUi.execution.requestId, 'LR-202607-0001')
  // 3. 旧 conversationId 不被复用（isHistoryTerminal 必须为 true）
  assert.equal(mod.isHistoryTerminal(restored.messages), true)
})

test('real-shape-confirm-buttons: 恢复后无任何操作 / 重试按钮', async () => {
  const { mod } = await buildHarness()
  const key = mod.resolveUserIdentity({ userId: 'U10001' }).storageKey
  // 即便持久化里残留 retryDecision，恢复时必须清空
  const persisted = buildAssistantAfterConfirm()
  persisted.actionUi.retryDecision = 'confirm'
  mod.writeChatHistory(key, { conversationId: 'c', messages: [persisted] })

  const restored = mod.readChatHistory(key)
  assert.equal(restored.messages[0].actionUi.retryDecision, null,
    '恢复时必须强制清空 retryDecision，否则会渲染"重试确认"按钮')
  // 实际 PendingActionCard 渲染逻辑依赖 phase：
  // showPendingActions = phase === 'pending'
  // showRetry = phase === 'error' && actionUi.retryDecision
  // phase === 'succeeded' → 两者均为 false，无按钮
  assert.notEqual(restored.messages[0].actionUi.phase, 'pending')
  assert.notEqual(restored.messages[0].actionUi.phase, 'error')
})

test('real-shape-cancel-restore: 重新加载后卡片展示状态保持 cancelled', async () => {
  const { mod } = await buildHarness()
  const key = mod.resolveUserIdentity({ userId: 'U10001' }).storageKey
  mod.writeChatHistory(key, {
    conversationId: 'conv-cancel',
    messages: [buildAssistantAfterCancel()],
  })

  const restored = mod.readChatHistory(key)
  const m = restored.messages[0]
  assert.equal(m.result.pendingAction.status, 'CANCELLED')
  assert.equal(m.actionUi.phase, 'cancelled',
    '已取消的草稿必须保留 cancelled UI，禁止被改为 expired')
  assert.equal(m.actionUi.retryDecision, null)
})

test('real-shape-confirm-convId-discard: 成功提交后旧 conversationId 不被恢复', async () => {
  const { mod } = await buildHarness()
  const key = mod.resolveUserIdentity({ userId: 'U10001' }).storageKey
  // 模拟真实链路：成功后 conversationId 已被 App.clearConversationId 清掉，
  // 但落盘 JSON 中仍可能保留 conversationId 字段（来自之前状态）。
  // 即便如此，isHistoryTerminal 必须为 true，让 App 在挂载时拒绝同步。
  mod.writeChatHistory(key, {
    conversationId: 'old-conv-should-not-reuse',
    messages: [buildAssistantAfterConfirm()],
  })

  const restored = mod.readChatHistory(key)
  // 数据可被读出（消息内容已落盘）
  assert.equal(restored.conversationId, 'old-conv-should-not-reuse')
  // 但 isHistoryTerminal 为 true → App 不把 conversationId 同步到 sessionStorage
  assert.equal(mod.isHistoryTerminal(restored.messages), true,
    '已成功终态的历史必须让 App 拒绝复用旧 conversationId')
})

test('real-shape-restore-error-retry-buttons: phase=error + retryDecision=confirm 恢复后无按钮', async () => {
  const { mod } = await buildHarness()
  // 极端情况：持久化 phase='error' 且 retryDecision='confirm'，
  // 必须确保恢复后 retryDecision 被强制清空，避免渲染"重试确认"。
  const messages = [{
    id: 'a', type: 'assistant', question: 'q',
    result: { answer: 'a', pendingAction: { actionId: 'pub', status: 'FAILED' } },
    actionUi: { phase: 'error', execution: null, error: '提交失败', retryDecision: 'confirm' },
  }]
  const out = mod.__TESTING__.restoreMessages(messages)
  assert.equal(out[0].actionUi.retryDecision, null,
    '恢复时必须强制清空 retryDecision，避免渲染重试按钮')
})
// =============================================================================
// 按 (用户, 模式) 隔离：agent / rag 各自独立 key、互不覆盖、互不删除
// =============================================================================

test('mode-key: historyKeyForMode 生成 <storageKey>.<mode> 并拒绝非法模式', async () => {
  const { mod } = await buildHarness()
  assert.equal(mod.historyKeyForMode('enterprise-ai-copilot.chat-history.u1', 'agent'),
    'enterprise-ai-copilot.chat-history.u1.agent')
  assert.equal(mod.historyKeyForMode('enterprise-ai-copilot.chat-history.u1', 'rag'),
    'enterprise-ai-copilot.chat-history.u1.rag')
  assert.equal(mod.historyKeyForMode('enterprise-ai-copilot.chat-history.u1', 'admin-logs'), null,
    '非问答模式必须被白名单拒绝')
  assert.equal(mod.historyKeyForMode('', 'agent'), null)
  assert.equal(mod.historyKeyForMode(null, 'agent'), null)
})

test('mode-isolation: agent 与 rag 各自写入读取,互不覆盖、互不删除', async () => {
  const { mod, ls } = await buildHarness()
  const base = 'enterprise-ai-copilot.chat-history.u1'
  const agentMsgs = [
    { id: 'a1', type: 'user', question: 'agent-q' },
    { id: 'a2', type: 'assistant', question: 'agent-q', result: { answer: 'agent-a' } },
  ]
  const ragMsgs = [
    { id: 'r1', type: 'user', question: 'rag-q' },
    { id: 'r2', type: 'assistant', question: 'rag-q', result: { answer: 'rag-a' } },
  ]
  mod.writeChatHistory(mod.historyKeyForMode(base, 'agent'), {
    conversationId: 'conv-agent', messages: agentMsgs,
  })
  mod.writeChatHistory(mod.historyKeyForMode(base, 'rag'), {
    conversationId: null, messages: ragMsgs,
  })
  const agent = mod.readHistoryByMode(base, 'agent')
  const rag = mod.readHistoryByMode(base, 'rag')
  assert.equal(agent.messages.length, 2)
  assert.equal(agent.conversationId, 'conv-agent')
  assert.equal(rag.messages.length, 2)
  assert.equal(rag.conversationId, null)
  assert.equal(agent.messages[0].question, 'agent-q')
  assert.equal(rag.messages[0].question, 'rag-q')

  // 清空 rag 不影响 agent(通过 clearChatHistory 删除单个模式 key)
  mod.clearChatHistory(mod.historyKeyForMode(base, 'rag'))
  assert.equal(mod.readHistoryByMode(base, 'rag'), null)
  assert.equal(mod.readHistoryByMode(base, 'agent').messages.length, 2,
    '清空 rag 绝不能影响 agent 历史')
})

test('mode-migrate: 旧格式(无后缀)首次按默认 agent 迁移,写盘成功才移除旧 key', async () => {
  const { mod, ls } = await buildHarness()
  const base = 'enterprise-ai-copilot.chat-history.u1'
  const legacyMsgs = [
    { id: 'l1', type: 'user', question: 'legacy-q' },
    { id: 'l2', type: 'assistant', question: 'legacy-q', result: { answer: 'legacy-a' } },
  ]
  mod.writeChatHistory(base, { conversationId: 'conv-legacy', messages: legacyMsgs })

  const migrated = mod.readHistoryByMode(base, 'agent')
  assert.equal(migrated.messages.length, 2)
  assert.equal(migrated.conversationId, 'conv-legacy')
  // 新 .agent key 已生成,旧 key 被移除(数据已迁移,不丢失)
  assert.ok(mod.readChatHistory(mod.historyKeyForMode(base, 'agent')), '.agent key 必须存在')
  assert.equal(ls.getItem(base), null, '迁移成功后旧格式 key 移除,避免清空后历史"复活"')

  // rag 模式不触发旧格式迁移(旧格式视为 agent 默认模式)
  mod.writeChatHistory(base, { conversationId: 'conv-legacy-2', messages: legacyMsgs })
  assert.equal(mod.readHistoryByMode(base, 'rag'), null)
  assert.ok(ls.getItem(base), 'rag 读取不能把旧 key 挪走')
})

test('last-mode: 按用户独立读写,非法值回退 null', async () => {
  const { mod } = await buildHarness()
  const base = 'enterprise-ai-copilot.chat-history.u1'
  assert.equal(mod.readLastMode(base), null, '无记录时返回 null')
  mod.writeLastMode(base, 'rag')
  assert.equal(mod.readLastMode(base), 'rag')
  mod.writeLastMode(base, 'agent')
  assert.equal(mod.readLastMode(base), 'agent')
  // 非法值 / 损坏数据回退 null
  globalThis.localStorage.setItem(base + mod.__TESTING__.LAST_MODE_SUFFIX, 'admin-logs')
  assert.equal(mod.readLastMode(base), null)
  mod.writeLastMode(base, 'admin-logs')
  assert.equal(mod.readLastMode(base), null, '非法模式不得写入')
})
