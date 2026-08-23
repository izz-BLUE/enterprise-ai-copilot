import { expect, test } from '@playwright/test'
import assert from 'node:assert/strict'

const TEST_NONCE = 'test-nonce-not-a-secret'
const TEST_ACTION_ID = 'act_business_action_test_1234567890'

test.beforeEach(async ({ page }) => {
  await page.addInitScript(({ key, state }) => {
    window.localStorage.setItem(key, JSON.stringify(state))
  }, {
    key: 'enterprise-ai-copilot.auth',
    state: { accessToken: 'test-token' },
  })
  await page.route('**/api/auth/me', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    headers: { 'Cache-Control': 'no-store' },
    body: JSON.stringify({ userId: 'U10001', username: 'zhangsan', employeeId: 'E10001', displayName: '张三', role: 'EMPLOYEE', enabled: true }),
  }))
})

const pendingResponse = (overrides = {}) => ({
  answer: '我已生成一份模拟年假申请草稿，请确认后提交。',
  route: 'action',
  category: 'business_action',
  success: true,
  safe: true,
  traceId: '11111111-1111-4111-8111-111111111111',
  pendingAction: {
    actionId: TEST_ACTION_ID,
    type: 'ANNUAL_LEAVE_REQUEST',
    status: 'PENDING_CONFIRMATION',
    title: '提交模拟年假申请',
    summary: {
      employee: 'Demo User',
      startDate: '2026-07-20',
      endDate: '2026-07-22',
      halfDay: 'NONE',
      days: 3,
      reason: '家庭事务',
      remainingBalanceBefore: 5,
      remainingBalanceAfter: 2,
    },
    confirmationNonce: TEST_NONCE,
    expiresAt: '2099-07-18T10:30:00Z',
    confirmationRequired: true,
    ...overrides,
  },
})

const executionResponse = (overrides = {}) => ({
  actionId: TEST_ACTION_ID,
  type: 'ANNUAL_LEAVE_REQUEST',
  status: 'SUCCEEDED',
  requestId: 'LR-202607-0001',
  message: '模拟年假申请已提交。',
  replayed: false,
  completedAt: '2026-07-18T10:00:00Z',
  originTraceId: '11111111-1111-4111-8111-111111111111',
  traceId: '22222222-2222-4222-8222-222222222222',
  ...overrides,
})

async function mockChat(page, response) {
  await page.route('**/api/agent/langgraph/chat', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    headers: { 'X-Conversation-Id': 'conv-from-chat' },
    body: JSON.stringify(response),
  }))
}

async function askForLeave(page) {
  const input = page.getByPlaceholder('输入问题... (Enter 发送，Shift+Enter 换行)')
  await input.fill('请帮我申请年假')
  await input.press('Enter')
}

async function openDraft(page, response = pendingResponse()) {
  await mockChat(page, response)
  await page.goto('/')
  await askForLeave(page)
  await expect(page.getByRole('region', { name: '年假申请确认卡' })).toBeVisible()
}

async function fillAdminToken(page, token = 'test-only') {
  await page.getByRole('button', { name: /管理员演示设置/ }).click()
  await page.getByPlaceholder('输入 Admin Token...').fill(token)
}

test('展示完整年假草稿且敏感确认数据不进入 DOM', async ({ page }) => {
  await openDraft(page)

  const card = page.getByRole('region', { name: '年假申请确认卡' })
  await expect(card.getByRole('heading', { name: '提交模拟年假申请' })).toBeVisible()
  await expect(card).toContainText('2026年07月20日')
  await expect(card).toContainText('2026年07月22日')
  await expect(card).toContainText('家庭事务')
  await expect(card).toContainText('3 天')
  await expect(card).toContainText('申请前余额')
  await expect(card).toContainText('5 天')
  await expect(card).toContainText('申请后余额')
  await expect(card).toContainText('2 天')
  await expect(card.getByRole('button', { name: '确认提交' })).toBeVisible()
  await expect(card.getByRole('button', { name: '取消草稿' })).toBeVisible()
  await expect(page.locator('body')).not.toContainText(TEST_NONCE)
  await expect(page.locator('body')).not.toContainText(TEST_ACTION_ID)
})

test('Agent请求携带Bearer身份且不发送Demo身份Header', async ({ page }) => {
  let captured
  await page.route('**/api/agent/langgraph/chat', async route => {
    captured = {
      headers: route.request().headers(),
      body: route.request().postDataJSON(),
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ answer: 'ok', route: 'rag', success: true }),
    })
  })
  await page.goto('/')
  await askForLeave(page)

  expect(captured.headers.authorization).toBe('Bearer test-token')
  expect(captured.headers['x-demo-user-id']).toBeUndefined()
  expect(captured.body).toEqual({ message: '请帮我申请年假' })
  expect(captured.body.userId).toBeUndefined()
  expect(captured.body.employeeId).toBeUndefined()
})

test('Confirm 只发送 nonce、Admin Token 和 UUID 幂等 Key', async ({ page }) => {
  let captured
  await page.route('**/api/agent/actions/**/confirm', async route => {
    captured = {
      url: route.request().url(),
      headers: route.request().headers(),
      body: route.request().postDataJSON(),
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(executionResponse()),
    })
  })

  await openDraft(page)
  await fillAdminToken(page)
  await page.getByRole('button', { name: '确认提交' }).click()

  await expect(page.getByText('模拟申请已提交')).toBeVisible()
  await expect(page.getByText('申请编号：LR-202607-0001')).toBeVisible()
  expect(new URL(captured.url).pathname).toBe(`/api/agent/actions/${TEST_ACTION_ID}/confirm`)
  expect(captured.headers['x-admin-token']).toBe('test-only')
  expect(captured.headers.authorization).toBe('Bearer test-token')
  expect(captured.headers['x-demo-user-id']).toBeUndefined()
  expect(captured.headers['idempotency-key']).toMatch(
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
  )
  expect(captured.body).toEqual({ confirmationNonce: TEST_NONCE })
})

test('同步双击 Confirm 只发出一个请求', async ({ page }) => {
  let requestCount = 0
  await page.route('**/api/agent/actions/**/confirm', async route => {
    requestCount += 1
    await new Promise(resolve => setTimeout(resolve, 100))
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(executionResponse()),
    })
  })

  await openDraft(page)
  const button = page.getByRole('button', { name: '确认提交' })
  await button.evaluate(element => {
    element.click()
    element.click()
  })
  await expect(page.getByText('模拟申请已提交')).toBeVisible()
  expect(requestCount).toBe(1)
})

test('同步双击 Cancel 只发出一个请求', async ({ page }) => {
  let requestCount = 0
  await page.route('**/api/agent/actions/**/cancel', async route => {
    requestCount += 1
    await new Promise(resolve => setTimeout(resolve, 100))
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(executionResponse({ status: 'CANCELLED', requestId: null })),
    })
  })

  await openDraft(page)
  const button = page.getByRole('button', { name: '取消草稿' })
  await button.evaluate(element => {
    element.click()
    element.click()
  })
  await expect(page.getByText('申请草稿已取消')).toBeVisible()
  expect(requestCount).toBe(1)
})

test('Confirm 503 后重试复用原 Idempotency-Key', async ({ page }) => {
  const keys = []
  let requestCount = 0
  await page.route('**/api/agent/actions/**/confirm', async route => {
    requestCount += 1
    keys.push(route.request().headers()['idempotency-key'])
    if (requestCount === 1) {
      await route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({
          actionId: TEST_ACTION_ID,
          status: 'PENDING_CONFIRMATION',
          errorCode: 'ACTION_INTERNAL_ERROR',
          message: '服务暂时不可用，请重试。',
        }),
      })
      return
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(executionResponse({ replayed: true })),
    })
  })

  await openDraft(page)
  await page.getByRole('button', { name: '确认提交' }).click()
  await expect(page.getByRole('button', { name: '重试确认' })).toBeVisible()
  await expect(page.getByRole('button', { name: '取消草稿' })).toHaveCount(0)
  await page.getByRole('button', { name: '重试确认' }).click()

  await expect(page.getByText('模拟申请已提交')).toBeVisible()
  await expect(page.getByText('本次响应为幂等重放，未重复创建申请。')).toBeVisible()
  expect(requestCount).toBe(2)
  expect(keys[0]).toBe(keys[1])
})

test('Cancel 不发送 Idempotency-Key 且成功后隐藏操作按钮', async ({ page }) => {
  let captured
  await page.route('**/api/agent/actions/**/cancel', async route => {
    captured = {
      headers: route.request().headers(),
      body: route.request().postDataJSON(),
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(executionResponse({ status: 'CANCELLED', requestId: null })),
    })
  })

  await openDraft(page)
  await fillAdminToken(page)
  await page.getByRole('button', { name: '取消草稿' }).click()

  await expect(page.getByText('申请草稿已取消')).toBeVisible()
  await expect(page.getByRole('button', { name: '确认提交' })).toHaveCount(0)
  expect(captured.headers['x-admin-token']).toBe('test-only')
  expect(captured.headers.authorization).toBe('Bearer test-token')
  expect(captured.headers['x-demo-user-id']).toBeUndefined()
  expect(captured.headers['idempotency-key']).toBeUndefined()
  expect(captured.body).toEqual({ confirmationNonce: TEST_NONCE })
})

test('Confirm执行期间退出登录按钮不可用', async ({ page }) => {
  let release
  const waiting = new Promise(resolve => { release = resolve })
  await page.route('**/api/agent/actions/**/confirm', async route => {
    await waiting
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(executionResponse()),
    })
  })
  await openDraft(page)

  const click = page.getByRole('button', { name: '确认提交' }).click()
  await expect(page.getByRole('button', { name: '退出登录' })).toBeDisabled()
  release()
  await click
  await expect(page.getByText('模拟申请已提交')).toBeVisible()
})

test('Confirm收到401后清理登录态与待确认敏感状态', async ({ page }) => {
  await page.route('**/api/agent/actions/**/confirm', route => route.fulfill({
    status: 401,
    contentType: 'application/json',
    body: JSON.stringify({ errorCode: 'AUTHENTICATION_REQUIRED', message: '请重新登录。' }),
  }))

  await openDraft(page)
  await page.getByRole('button', { name: '确认提交' }).click()

  await expect(page.getByRole('heading', { name: '登录工作台' })).toBeVisible()
  await expect(page.evaluate(key => window.localStorage.getItem(key), 'enterprise-ai-copilot.auth'))
    .resolves.toBeNull()
  await expect(page.locator('body')).not.toContainText(TEST_NONCE)
  await expect(page.locator('body')).not.toContainText(TEST_ACTION_ID)
})

test('标准RAG携带Bearer身份且不发送Demo身份Header', async ({ page }) => {
  let captured
  await page.route('**/api/chat', async route => {
    captured = {
      headers: route.request().headers(),
      body: route.request().postDataJSON(),
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ answer: '标准问答', success: true, traceId: 'rag-trace' }),
    })
  })
  await page.goto('/')
  await expect(page.locator('#root')).toBeAttached()
  await page.getByRole('button', { name: '切换到标准问答' }).click()
  const input = page.getByPlaceholder('输入问题... (Enter 发送，Shift+Enter 换行)')
  await input.fill('几点上班？')
  await input.press('Enter')

  await expect(page.getByText('标准问答', { exact: true }).last()).toBeVisible()
  expect(captured.headers.authorization).toBe('Bearer test-token')
  expect(captured.headers['x-demo-user-id']).toBeUndefined()
  expect(captured.headers['x-admin-token']).toBeUndefined()
  expect(captured.body).toEqual({ message: '几点上班？' })
  expect(captured.body.userId).toBeUndefined()
  expect(captured.body.employeeId).toBeUndefined()
})

test('服务端 ACTION_EXPIRED 进入过期终态', async ({ page }) => {
  // 真实 Java：BusinessActionService.error() 把 PendingAction.status 作为
  // ActionErrorResponse.body.status 返回；expire 路径 action.status() === EXPIRED。
  await page.route('**/api/agent/actions/**/confirm', route => route.fulfill({
    status: 409,
    contentType: 'application/json',
    body: JSON.stringify({
      actionId: TEST_ACTION_ID,
      status: 'EXPIRED',
      errorCode: 'ACTION_EXPIRED',
      message: '草稿已过期。',
    }),
  }))

  await openDraft(page)
  await page.getByRole('button', { name: '确认提交' }).click()

  await expect(page.getByRole('region', { name: '年假申请确认卡' }).locator('.action-status')).toHaveText('已过期')
  await expect(page.getByText(/请重新发送年假申请生成新草稿/)).toBeVisible()
  await expect(page.getByRole('button', { name: '确认提交' })).toHaveCount(0)
})

test('本地已过期草稿不能发送 Confirm 或 Cancel', async ({ page }) => {
  let decisionRequests = 0
  await page.route('**/api/agent/actions/**', route => {
    decisionRequests += 1
    return route.abort()
  })

  await openDraft(page, pendingResponse({ expiresAt: '2020-01-01T00:00:00Z' }))
  await expect(page.getByRole('region', { name: '年假申请确认卡' }).locator('.action-status')).toHaveText('已过期')
  await expect(page.getByRole('button', { name: '确认提交' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '取消草稿' })).toHaveCount(0)
  expect(decisionRequests).toBe(0)
})

test('ADMIN_REQUIRED 显示安全错误并允许补 Token 后重试', async ({ page }) => {
  let requestCount = 0
  await page.route('**/api/agent/actions/**/confirm', async route => {
    requestCount += 1
    if (requestCount === 1) {
      await route.fulfill({
        status: 403,
        contentType: 'application/json',
        body: JSON.stringify({
          actionId: TEST_ACTION_ID,
          status: 'PENDING_CONFIRMATION',
          errorCode: 'ADMIN_REQUIRED',
          message: '需要管理员权限。',
        }),
      })
      return
    }
    expect(route.request().headers()['x-admin-token']).toBe('test-only')
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(executionResponse()),
    })
  })

  await openDraft(page)
  await page.getByRole('button', { name: '确认提交' }).click()
  await expect(page.getByRole('alert')).toContainText('需要管理员权限。')
  await expect(page.locator('body')).not.toContainText(TEST_NONCE)
  await expect(page.locator('body')).not.toContainText(/Exception|stack trace/i)

  await fillAdminToken(page)
  await page.getByRole('button', { name: '重试确认' }).click()
  await expect(page.getByText('模拟申请已提交')).toBeVisible()
})

test('缺字段澄清响应不展示确认卡', async ({ page }) => {
  await mockChat(page, {
    answer: '请补充年假的开始日期和申请原因。',
    route: 'action',
    category: 'business_action',
    success: true,
    missing_fields: ['start_date', 'reason'],
  })
  await page.goto('/')
  await askForLeave(page)

  await expect(page.getByText('请补充年假的开始日期和申请原因。')).toBeVisible()
  await expect(page.getByRole('region', { name: '年假申请确认卡' })).toHaveCount(0)
})

test('未知 Action 类型显示安全提示且无操作按钮', async ({ page }) => {
  await mockChat(page, pendingResponse({ type: 'UNKNOWN_WRITE_ACTION' }))
  await page.goto('/')
  await askForLeave(page)

  await expect(page.getByRole('alert')).toContainText('不支持此操作类型')
  await expect(page.getByRole('button', { name: '确认提交' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '取消草稿' })).toHaveCount(0)
  await expect(page.locator('body')).not.toContainText(TEST_NONCE)
})

// ----------------------------------------------------------------------------
// 本地历史恢复：真实业务终态（SUCCEEDED / CANCELLED）必须保留终态 UI
// 且旧 conversationId 不再被复用。
// ----------------------------------------------------------------------------

const STORAGE_KEY_PREFIX = 'enterprise-ai-copilot.chat-history.'

const readChatHistoryRecord = async (page, userId) => {
  return page.evaluate(({ key }) => {
    const raw = window.localStorage.getItem(key)
    return raw ? JSON.parse(raw) : null
  }, { key: `${STORAGE_KEY_PREFIX}${userId}` })
}

const clearChatHistory = async (page, userId) => {
  await page.evaluate(({ key }) => window.localStorage.removeItem(key),
    { key: `${STORAGE_KEY_PREFIX}${userId}` })
}

test('Confirm 成功后 reload: 卡片仍显示已提交 + 申请编号 + 无任何按钮 + conversationId 不复用', async ({ page }) => {
  let confirmCallCount = 0
  let conversationIdAfterConfirm = null

  await page.route('**/api/agent/actions/**/confirm', async route => {
    confirmCallCount += 1
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: { 'X-Conversation-Id': 'conv-after-success' },
      body: JSON.stringify(executionResponse({ requestId: 'LR-202607-0001' })),
    })
  })

  await openDraft(page)
  const beforeConvId = await page.evaluate(() => window.sessionStorage.getItem('enterprise-ai-copilot.conversation-id'))
  await page.getByRole('button', { name: '确认提交' }).click()
  await expect(page.getByText('模拟申请已提交')).toBeVisible()
  await expect(page.getByText('申请编号：LR-202607-0001')).toBeVisible()

  // 1) 真实落盘：pendingAction.status 已被 App 同步为 SUCCEEDED
  const record = await readChatHistoryRecord(page, 'U10001')
  assert.ok(record, 'localStorage 必须存在落盘记录')
  const persisted = record.messages.find(m => m.type === 'assistant')
  assert.equal(persisted.result.pendingAction.status, 'SUCCEEDED',
    'Java SUCCEEDED 后 pendingAction.status 必须同步为 SUCCEEDED')
  assert.equal(persisted.actionUi.phase, 'succeeded')
  assert.equal(persisted.actionUi.execution.requestId, 'LR-202607-0001',
    '公开申请编号 requestId 应保留')
  assert.ok(!('confirmationNonce' in persisted.result.pendingAction),
    'confirmationNonce 永不入盘')

  // 2) Java 已通过 X-Conversation-Id 切换会话命名空间
  conversationIdAfterConfirm = await page.evaluate(() => window.sessionStorage.getItem('enterprise-ai-copilot.conversation-id'))
  assert.notEqual(conversationIdAfterConfirm, beforeConvId,
    '成功后服务端应下发新的 conversationId（客户端立即丢弃旧 ID）')

  // 3) Reload 后卡片仍显示已提交 + 申请编号 + 无任何按钮
  await page.reload()
  await expect(page.getByText('模拟申请已提交')).toBeVisible()
  await expect(page.getByText('申请编号：LR-202607-0001')).toBeVisible()
  await expect(page.getByRole('button', { name: '确认提交' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '取消草稿' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '重试确认' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '重试取消' })).toHaveCount(0)
  await expect(page.locator('body')).not.toContainText(TEST_NONCE)
  await expect(page.locator('body')).not.toContainText(TEST_ACTION_ID)

  // 4) Reload 后 conversationId 不被恢复（终态历史 → 新业务任务用全新会话）
  //    localStorage 中 conversationId 已被 clearConversationId 主动置空
  //    （持久化层与内存保持一致），App 不会再把它同步到 sessionStorage。
  const convIdAfterReload = await page.evaluate(() => window.sessionStorage.getItem('enterprise-ai-copilot.conversation-id'))
  assert.equal(convIdAfterReload, null,
    '终态历史 reload 后 sessionStorage 必须为空，让下一次任务走全新会话')

  // localStorage 中 conversationId 也保持 null（与内存一致）
  const recordAfterReload = await readChatHistoryRecord(page, 'U10001')
  assert.equal(recordAfterReload.conversationId, null,
    '成功后 conversationId 已被 clearConversationId 主动置空，与内存一致')
  assert.equal(recordAfterReload.messages.find(m => m.type === 'assistant')
    .result.pendingAction.status, 'SUCCEEDED',
    '消息本身仍完整保留，pendingAction.status 同步为 SUCCEEDED')

  // 5) 仅触发 1 次真实 confirm（reload 不应再发起任何业务请求）
  assert.equal(confirmCallCount, 1)
})

test('Cancel 成功后 reload: 卡片仍显示已取消 + 无按钮 + conversationId 不复用', async ({ page }) => {
  let cancelCallCount = 0

  await page.route('**/api/agent/actions/**/cancel', async route => {
    cancelCallCount += 1
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: { 'X-Conversation-Id': 'conv-after-cancel' },
      body: JSON.stringify(executionResponse({ status: 'CANCELLED', requestId: null })),
    })
  })

  await openDraft(page)
  await page.getByRole('button', { name: '取消草稿' }).click()
  await expect(page.getByText('申请草稿已取消')).toBeVisible()

  // 1) 真实落盘
  const record = await readChatHistoryRecord(page, 'U10001')
  const persisted = record.messages.find(m => m.type === 'assistant')
  assert.equal(persisted.result.pendingAction.status, 'CANCELLED',
    'Java CANCELLED 后 pendingAction.status 必须同步为 CANCELLED')
  assert.equal(persisted.actionUi.phase, 'cancelled',
    '已取消的草稿必须保留 cancelled UI，禁止被改为 expired')

  // 2) Reload 后状态保持
  await page.reload()
  await expect(page.getByText('申请草稿已取消')).toBeVisible()
  await expect(page.getByRole('button', { name: '确认提交' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '取消草稿' })).toHaveCount(0)
  await expect(page.locator('body')).not.toContainText(TEST_NONCE)

  // 3) 终态后 conversationId 不被恢复
  const convIdAfterReload = await page.evaluate(() => window.sessionStorage.getItem('enterprise-ai-copilot.conversation-id'))
  assert.equal(convIdAfterReload, null,
    '终态历史 reload 后 sessionStorage 必须为空，让下一次任务走全新会话')

  assert.equal(cancelCallCount, 1)
})

test('已成功卡片在 reload 后即便持久化中残留 retryDecision 也无重试按钮', async ({ page }) => {
  // 先走完整链路到 succeeded
  await page.route('**/api/agent/actions/**/confirm', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(executionResponse({ requestId: 'LR-202607-0002', replayed: true })),
    })
  })
  await openDraft(page)
  await page.getByRole('button', { name: '确认提交' }).click()
  await expect(page.getByText('模拟申请已提交')).toBeVisible()

  // 注入恶意 / 错误持久化：把 retryDecision 强行写回 'confirm'
  await page.evaluate(({ key }) => {
    const raw = window.localStorage.getItem(key)
    const record = JSON.parse(raw)
    const assistant = record.messages.find(m => m.type === 'assistant')
    assistant.actionUi.retryDecision = 'confirm'
    window.localStorage.setItem(key, JSON.stringify(record))
  }, { key: `${STORAGE_KEY_PREFIX}U10001` })

  await page.reload()
  await expect(page.getByText('模拟申请已提交')).toBeVisible()
  await expect(page.getByText('申请编号：LR-202607-0002')).toBeVisible()
  await expect(page.getByRole('button', { name: '确认提交' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '重试确认' })).toHaveCount(0,
    '即便持久化中残留 retryDecision，恢复时必须强制清空，绝不能渲染重试按钮')
})

// ----------------------------------------------------------------------------
// 错误响应中的权威终态：仅白名单 status 同步 + 持久化终态时丢弃 conversationId
// ----------------------------------------------------------------------------

test('Java ACTION_EXPIRED + status=EXPIRED → 持久化为 EXPIRED + reload 后 conversationId 不恢复', async ({ page }) => {
  await page.route('**/api/agent/actions/**/confirm', route => route.fulfill({
    status: 409,
    contentType: 'application/json',
    body: JSON.stringify({
      actionId: TEST_ACTION_ID,
      status: 'EXPIRED',
      errorCode: 'ACTION_EXPIRED',
      message: '草稿已过期。',
    }),
  }))

  await openDraft(page)
  const beforeConvId = await page.evaluate(() => window.sessionStorage.getItem('enterprise-ai-copilot.conversation-id'))
  await page.getByRole('button', { name: '确认提交' }).click()

  // 1) UI 进入已过期终态
  await expect(page.getByRole('region', { name: '年假申请确认卡' }).locator('.action-status')).toHaveText('已过期')
  await expect(page.getByText(/请重新发送年假申请生成新草稿/)).toBeVisible()
  await expect(page.getByRole('button', { name: '确认提交' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '取消草稿' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '重试确认' })).toHaveCount(0)

  // 2) localStorage 落盘：pendingAction.status = EXPIRED, actionUi.phase = expired
  const record = await readChatHistoryRecord(page, 'U10001')
  assert.ok(record, 'localStorage 必须存在落盘记录')
  const persisted = record.messages.find(m => m.type === 'assistant')
  assert.equal(persisted.result.pendingAction.status, 'EXPIRED',
    'Java status=EXPIRED 必须同步到 pendingAction.status')
  assert.equal(persisted.actionUi.phase, 'expired',
    'actionUi.phase 必须为 expired')
  assert.equal(persisted.actionUi.retryDecision, null,
    '权威终态必须清空 retryDecision')
  assert.equal(record.conversationId, null,
    'conversationId 必须被清除（持久化层与内存一致）')

  // 3) sessionStorage 也已被清除
  const afterConvId = await page.evaluate(() => window.sessionStorage.getItem('enterprise-ai-copilot.conversation-id'))
  assert.notEqual(afterConvId, beforeConvId,
    '终态响应后服务端会下发新 conversationId（前端丢弃旧 ID）')

  // 4) reload 后：卡片保持过期态 + 不恢复旧 conversationId
  await page.reload()
  await expect(page.getByRole('region', { name: '年假申请确认卡' }).locator('.action-status')).toHaveText('已过期')
  const convIdAfterReload = await page.evaluate(() => window.sessionStorage.getItem('enterprise-ai-copilot.conversation-id'))
  assert.equal(convIdAfterReload, null,
    '终态历史 reload 后 sessionStorage 必须为空')
})

test('Java ACTION_STATE_CONFLICT + status=FAILED → 持久化为 FAILED + reload 后 conversationId 不恢复', async ({ page }) => {
  await page.route('**/api/agent/actions/**/confirm', route => route.fulfill({
    status: 409,
    contentType: 'application/json',
    body: JSON.stringify({
      actionId: TEST_ACTION_ID,
      status: 'FAILED',
      errorCode: 'ACTION_STATE_CONFLICT',
      message: '申请状态已变化。',
    }),
  }))

  await openDraft(page)
  await page.getByRole('button', { name: '确认提交' }).click()

  // 1) UI 进入 error 终态（FAILED → phase=error → 文案"处理失败"）
  await expect(page.getByRole('region', { name: '年假申请确认卡' }).locator('.action-status')).toHaveText('处理失败')
  await expect(page.getByRole('button', { name: '确认提交' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '重试确认' })).toHaveCount(0)

  // 2) localStorage：pendingAction.status = FAILED
  const record = await readChatHistoryRecord(page, 'U10001')
  const persisted = record.messages.find(m => m.type === 'assistant')
  assert.equal(persisted.result.pendingAction.status, 'FAILED',
    'Java status=FAILED 必须同步到 pendingAction.status')
  assert.equal(record.conversationId, null,
    'FAILED 终态后 conversationId 必须被清除')

  // 3) reload 后不恢复 conversationId
  await page.reload()
  await expect(page.getByRole('region', { name: '年假申请确认卡' }).locator('.action-status')).toHaveText('处理失败')
  const convIdAfterReload = await page.evaluate(() => window.sessionStorage.getItem('enterprise-ai-copilot.conversation-id'))
  assert.equal(convIdAfterReload, null,
    'FAILED 终态历史 reload 后 sessionStorage 必须为空')
})

test('Java INVALID_CONFIRMATION_NONCE + status=PENDING_CONFIRMATION → 不当终态处理，不清除 conversationId', async ({ page }) => {
  await page.route('**/api/agent/actions/**/confirm', route => route.fulfill({
    status: 409,
    contentType: 'application/json',
    body: JSON.stringify({
      actionId: TEST_ACTION_ID,
      status: 'PENDING_CONFIRMATION',
      errorCode: 'INVALID_CONFIRMATION_NONCE',
      message: 'nonce 已失效。',
    }),
  }))

  await openDraft(page)
  const beforeConvId = await page.evaluate(() => window.sessionStorage.getItem('enterprise-ai-copilot.conversation-id'))
  await page.getByRole('button', { name: '确认提交' }).click()

  // 1) UI 进入 error 状态，但 pendingAction.status 必须保持 PENDING_CONFIRMATION
  await expect(page.getByRole('region', { name: '年假申请确认卡' }).locator('.action-status')).toHaveText('处理失败')

  const record = await readChatHistoryRecord(page, 'U10001')
  const persisted = record.messages.find(m => m.type === 'assistant')
  assert.equal(persisted.result.pendingAction.status, 'PENDING_CONFIRMATION',
    'INVALID_CONFIRMATION_NONCE + status=PENDING_CONFIRMATION 不能推断为 FAILED')
  assert.equal(record.conversationId, beforeConvId,
    '非权威终态不能清除 conversationId')

  // 2) 不允许使用已失效的 nonce 继续重试：retryDecision 必须为 null（因为此错误非 retryable）
  await expect(page.getByRole('button', { name: '重试确认' })).toHaveCount(0)
})

test('Java ACTION_NOT_FOUND 且 status 缺失 → 不猜测终态，不清除 conversationId', async ({ page }) => {
  await page.route('**/api/agent/actions/**/confirm', route => route.fulfill({
    status: 404,
    contentType: 'application/json',
    body: JSON.stringify({
      actionId: TEST_ACTION_ID,
      errorCode: 'ACTION_NOT_FOUND',
      message: '申请草稿不存在。',
    }),
  }))

  await openDraft(page)
  const beforeConvId = await page.evaluate(() => window.sessionStorage.getItem('enterprise-ai-copilot.conversation-id'))
  await page.getByRole('button', { name: '确认提交' }).click()

  await expect(page.getByRole('region', { name: '年假申请确认卡' }).locator('.action-status')).toHaveText('处理失败')

  const record = await readChatHistoryRecord(page, 'U10001')
  const persisted = record.messages.find(m => m.type === 'assistant')
  assert.equal(persisted.result.pendingAction.status, 'PENDING_CONFIRMATION',
    '没有 status 字段或 status 不在白名单时，绝不能推断为 FAILED')
  assert.equal(record.conversationId, beforeConvId,
    '非权威终态不能清除 conversationId')
})

test('前端 expiresAt 本地计时器触发 → 只改变 actionUi，不写 EXPIRED 到 pendingAction.status，不清除 conversationId', async ({ page }) => {
  await openDraft(page, pendingResponse({ expiresAt: '2020-01-01T00:00:00Z' }))
  const beforeConvId = await page.evaluate(() => window.sessionStorage.getItem('enterprise-ai-copilot.conversation-id'))

  // 1) 本地计时器触发：UI 显示已过期，按钮全部消失
  await expect(page.getByRole('region', { name: '年假申请确认卡' }).locator('.action-status')).toHaveText('已过期')
  await expect(page.getByRole('button', { name: '确认提交' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '取消草稿' })).toHaveCount(0)

  // 2) localStorage：actionUi.phase=expired，但 pendingAction.status 仍为 PENDING_CONFIRMATION
  const record = await readChatHistoryRecord(page, 'U10001')
  const persisted = record.messages.find(m => m.type === 'assistant')
  assert.equal(persisted.result.pendingAction.status, 'PENDING_CONFIRMATION',
    '本地计时器不得把 EXPIRED 写入 pendingAction.status（必须是 Java 权威响应）')
  assert.equal(persisted.actionUi.phase, 'expired',
    '本地计时器可改变 UI 显示（actionUi.phase=expired）')

  // 3) conversationId 不被本地计时器清除
  const afterConvId = await page.evaluate(() => window.sessionStorage.getItem('enterprise-ai-copilot.conversation-id'))
  assert.equal(afterConvId, beforeConvId,
    '本地计时器不得清除 conversationId（必须由 Java 权威终态驱动）')
})
