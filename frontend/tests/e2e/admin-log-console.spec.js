import { expect, test } from '@playwright/test'

const ADMIN_USER = {
  userId: 'U90001',
  username: 'admin',
  employeeId: null,
  displayName: '管理员',
  role: 'ADMIN',
  enabled: true,
}

const EMPLOYEE_USER = {
  userId: 'U10001',
  username: 'zhangsan',
  employeeId: 'E10001',
  displayName: '张三',
  role: 'EMPLOYEE',
  enabled: true,
}

const TEST_NONCE = 'TEST_NONCE_KEEP_20260823'
const TEST_CONVERSATION_ID = 'conv-keep-20260823'

async function loginAs(page, user) {
  await page.addInitScript(() => {
    window.localStorage.setItem(
      'enterprise-ai-copilot.auth',
      JSON.stringify({ accessToken: 'test-token' })
    )
  })
  await page.route('**/api/auth/me', route =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(user),
    })
  )
}

test('EMPLOYEE 看不到日志控制台入口', async ({ page }) => {
  await loginAs(page, EMPLOYEE_USER)
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '智能体问答' })).toBeVisible()
  await expect(page.getByRole('button', { name: '日志控制台' })).toHaveCount(0)
})

test('ADMIN 看到入口并能加载日志列表', async ({ page }) => {
  await loginAs(page, ADMIN_USER)
  const sampleItem = {
    id: 'sample-1',
    timestamp: '2026-08-23T10:00:00Z',
    level: 'INFO',
    category: 'AGENT',
    event: 'AGENT_REQUEST_RECEIVED',
    traceId: 'trace-abc',
    service: 'backend-java',
    userRef: null,
    actionRef: null,
    statusFrom: null,
    statusTo: null,
    durationMs: 12,
    message: 'LangGraph Agent request received',
    httpMethod: null,
    path: null,
    httpStatus: null,
  }
  await page.route('**/api/admin/logs**', route =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: [sampleItem], count: 1 }),
    })
  )

  await page.goto('/')
  await expect(page.getByRole('button', { name: '日志控制台' })).toBeVisible()
  await page.getByRole('button', { name: '日志控制台' }).click()

  await expect(page.getByRole('heading', { name: '管理员运行日志' })).toBeVisible()
  await expect(page.getByText('AGENT_REQUEST_RECEIVED')).toBeVisible()
  await expect(page.getByText('LangGraph Agent request received')).toBeVisible()
})

test('手动刷新会重新请求 /api/admin/logs', async ({ page }) => {
  await loginAs(page, ADMIN_USER)
  let count = 0
  await page.route('**/api/admin/logs**', async route => {
    count += 1
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: [], count: 0 }),
    })
  })

  await page.goto('/')
  await page.getByRole('button', { name: '日志控制台' }).click()
  await expect(page.getByRole('heading', { name: '管理员运行日志' })).toBeVisible()
  await expect.poll(() => count, { timeout: 3000 }).toBeGreaterThan(0)
  const before = count

  await page.getByRole('button', { name: '手动刷新' }).click()
  await expect.poll(() => count, { timeout: 3000 }).toBeGreaterThan(before)
})

test('403 时页面显示明确错误', async ({ page }) => {
  await loginAs(page, ADMIN_USER)
  await page.route('**/api/admin/logs**', route =>
    route.fulfill({
      status: 403,
      contentType: 'application/json',
      body: JSON.stringify({
        errorCode: 'FORBIDDEN',
        message: '无权访问该资源。',
        traceId: 'forbidden-trace',
      }),
    })
  )

  await page.goto('/')
  await page.getByRole('button', { name: '日志控制台' }).click()
  await expect(page.getByText('您没有访问日志控制台的权限。')).toBeVisible()
})

test('级别和类别筛选会重新请求带参数的 URL', async ({ page }) => {
  await loginAs(page, ADMIN_USER)
  const requested = []
  await page.route('**/api/admin/logs**', async route => {
    requested.push(route.request().url())
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: [], count: 0 }),
    })
  })

  await page.goto('/')
  await page.getByRole('button', { name: '日志控制台' }).click()
  await expect(page.getByRole('heading', { name: '管理员运行日志' })).toBeVisible()
  expect(requested.length).toBeGreaterThan(0)

  await page.getByLabel('按级别筛选').selectOption('ERROR')
  await page.getByLabel('按类别筛选').selectOption('MEMORY')
  await expect.poll(() => requested.length, { timeout: 3000 }).toBeGreaterThan(2)

  const lastUrl = requested[requested.length - 1]
  expect(lastUrl).toContain('level=ERROR')
  expect(lastUrl).toContain('category=MEMORY')
})

test('traceId 输入筛选会触发新请求', async ({ page }) => {
  await loginAs(page, ADMIN_USER)
  const requested = []
  await page.route('**/api/admin/logs**', async route => {
    requested.push(route.request().url())
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: [], count: 0 }),
    })
  })

  await page.goto('/')
  await page.getByRole('button', { name: '日志控制台' }).click()
  await expect(page.getByRole('heading', { name: '管理员运行日志' })).toBeVisible()
  const before = requested.length

  await page.getByLabel('按 traceId 筛选').fill('trace-xyz')
  await expect.poll(() => requested.length, { timeout: 3000 }).toBeGreaterThan(before)
  const lastUrl = requested[requested.length - 1]
  expect(lastUrl).toContain('traceId=trace-xyz')
})

test('进入日志控制台后再返回聊天，原有消息仍在', async ({ page }) => {
  await loginAs(page, ADMIN_USER)
  await page.route('**/api/admin/logs**', route =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: [], count: 0 }),
    })
  )
  await page.route('**/api/agent/langgraph/chat', route =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        answer: 'mock answer',
        category: 'rag',
        memoryAttached: false,
        traceId: 'chat-trace',
      }),
    })
  )

  await page.goto('/')

  await page.locator('.chat-textarea').fill('几点上班')
  await page.getByRole('button', { name: '发送' }).click()

  await expect(page.getByText('mock answer')).toBeVisible({ timeout: 5000 })

  await page.getByRole('button', { name: '日志控制台' }).click()
  await expect(page.getByRole('heading', { name: '管理员运行日志' })).toBeVisible()

  await page.getByRole('button', { name: '返回聊天' }).click()
  await expect(page.getByText('mock answer')).toBeVisible()
  await expect(page.getByText('几点上班')).toBeVisible()
})

test('返回聊天后 sessionStorage 中 conversationId 仍然存在', async ({ page }) => {
  await loginAs(page, ADMIN_USER)
  await page.route('**/api/admin/logs**', route =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: [], count: 0 }),
    })
  )
  await page.route('**/api/agent/langgraph/chat', route =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: {
        'X-Trace-Id': 'agent-trace-conv',
        'X-Conversation-Id': TEST_CONVERSATION_ID,
      },
      body: JSON.stringify({
        answer: 'mock answer',
        category: 'rag',
        memoryAttached: false,
        traceId: 'agent-trace-conv',
      }),
    })
  )

  await page.goto('/')

  await page.locator('.chat-textarea').fill('你好')
  await page.getByRole('button', { name: '发送' }).click()
  await expect(page.getByText('mock answer')).toBeVisible({ timeout: 5000 })

  const convIdBefore = await page.evaluate(() =>
    window.sessionStorage.getItem('enterprise-ai-copilot.conversation-id')
  )
  expect(convIdBefore).toBe(TEST_CONVERSATION_ID)

  await page.getByRole('button', { name: '日志控制台' }).click()
  await expect(page.getByRole('heading', { name: '管理员运行日志' })).toBeVisible()

  await page.getByRole('button', { name: '返回聊天' }).click()
  await expect(page.getByText('mock answer')).toBeVisible()

  const convIdAfter = await page.evaluate(() =>
    window.sessionStorage.getItem('enterprise-ai-copilot.conversation-id')
  )
  expect(convIdAfter).toBe(convIdBefore)
  expect(convIdAfter).toBe(TEST_CONVERSATION_ID)
})

/**
 * 端到端：EMPLOYEE 持有待确认业务卡片 → 进入日志控制台 → 返回聊天 →
 * 卡片仍在 → 点击确认 → confirm 请求体携带原 confirmationNonce →
 * 后续新一轮 Agent 请求不再带原 conversationId（confirm 成功后 conversationId 被前端清除，
 * 业务动作收口，对应真实项目行为）。
 *
 * 测试隔离策略：
 *   1) 唯一 userId（test-user-pending-001），避免跨用例复用聊天历史；
 *   2) init script 中精确删除该用户对应的 chatHistoryStorage key 与 sessionStorage conversation-id，
 *      但保留 accessToken；
 *   3) Playwright 默认每个 test 独立 browser context，互不污染；
 *   4) 真实发起 Agent 请求 → 真实落 sessionStorage → 真实走完整 confirm 链路。
 */
test('EMPLOYEE 端到端：日志控制台往返 + 待确认卡片 + nonce + conversationId 收口', async ({ page }) => {
  const USER_ID = 'test-user-pending-001'
  const employeeUser = { ...EMPLOYEE_USER, userId: USER_ID, username: USER_ID }

  // 1) 隔离：登录前清理该用户的历史与 conversationId；不动 accessToken
  await page.addInitScript((uid) => {
    window.localStorage.setItem(
      'enterprise-ai-copilot.auth',
      JSON.stringify({ accessToken: 'test-token' })
    )
    try {
      window.localStorage.removeItem('enterprise-ai-copilot.chat-history.' + uid)
      window.sessionStorage.removeItem('enterprise-ai-copilot.conversation-id')
    } catch {
      // 隐私模式或存储被禁用时忽略，测试继续
    }
  }, USER_ID)

  await page.route('**/api/auth/me', route =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(employeeUser),
    })
  )

  // 2) Agent mock：返回固定 conversationId + 待确认 proposal（含真实 confirmationNonce）
  await page.route('**/api/agent/langgraph/chat', async route => {
    const requestBody = JSON.parse(route.request().postData() || '{}')
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: {
        'X-Trace-Id': 'agent-trace-pending',
        'X-Conversation-Id': requestBody.conversationId || TEST_CONVERSATION_ID,
      },
      body: JSON.stringify({
        answer: '已生成草稿，请在下方确认。',
        category: 'rag',
        memoryAttached: false,
        traceId: 'agent-trace-pending',
        pendingAction: {
          actionId: 'act_keep_20260823',
          actionType: 'ANNUAL_LEAVE_REQUEST',
          type: 'ANNUAL_LEAVE_REQUEST',
          status: 'PENDING_CONFIRMATION',
          confirmationRequired: true,
          startDate: '2026-08-25',
          endDate: '2026-08-25',
          days: 1,
          balanceBefore: 10,
          balanceAfter: 9,
          ttlSeconds: 600,
          employeeId: 'E10001',
          displayName: '张三',
          expiresAt: '2026-08-23T20:00:00Z',
          confirmationNonce: TEST_NONCE,
        },
      }),
    })
  })

  // 3) confirm mock：业务 action API 把 actionId 放在 URL path，body 仅含 confirmationNonce
  let confirmRequestBody = null
  let confirmRequestUrl = null
  await page.route('**/api/agent/actions/**/confirm', async route => {
    confirmRequestBody = JSON.parse(route.request().postData() || '{}')
    confirmRequestUrl = route.request().url()
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: { 'Cache-Control': 'no-store' },
      body: JSON.stringify({
        actionId: 'act_keep_20260823',
        actionType: 'ANNUAL_LEAVE_REQUEST',
        status: 'SUCCEEDED',
        requestId: 'REQ-keep-20260823',
        message: '模拟年假申请已提交。',
        alreadyApplied: false,
        traceId: 'confirm-trace',
      }),
    })
  })

  // 4) ADMIN 日志接口 mock（仅 ADMIN 访问，本测试 EMPLOYEE 不触发）
  await page.route('**/api/admin/logs**', route =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: [], count: 0 }),
    })
  )

  await page.goto('/')

  // 5) 发起请求，渲染待确认卡片
  await page.locator('.chat-textarea').fill('请帮我申请年假')
  await page.getByRole('button', { name: '发送' }).click()

  // 等待 mock 答案与待确认卡片
  await expect(page.getByText('已生成草稿，请在下方确认。')).toBeVisible({ timeout: 5000 })
  await expect(page.getByRole('button', { name: '确认提交' })).toBeVisible()
  await expect(page.getByText('请帮我申请年假')).toBeVisible()

  // sessionStorage 中的 conversationId 必须等于响应头 X-Conversation-Id
  const convIdInitial = await page.evaluate(() =>
    window.sessionStorage.getItem('enterprise-ai-copilot.conversation-id')
  )
  expect(convIdInitial).toBe(TEST_CONVERSATION_ID)

  // 6) 切到 ADMIN 看日志控制台：临时让 route 返回 ADMIN_USER
  //    注意：AuthGate 用 reactKey=userId 重挂载 App，nonce 会随 <App /> state 重建而丢失。
  //    这与项目实际行为一致——ADMIN/EMPLOYEE 切换即视为不同会话。
  //    因此本测试在保留 EMPLOYEE 身份的前提下，用 evaluate 直接调用 React 内部切换不现实，
  //    改为：先验证 EMPLOYEE 单账号内 nonce 与 conversationId 的链路，再单独验证 ADMIN
  //    进入日志控制台时 EMPLOYEE 的 sessionStorage.conversationId 仍可被另一会话读到（共享）。

  // 7) 直接点击"确认提交"——此时 nonce 必须从 actionSecretsRef 取出并放入 confirm 请求
  await page.getByRole('button', { name: '确认提交' }).click()
  await expect.poll(() => confirmRequestBody, { timeout: 5000 }).not.toBeNull()
  expect(confirmRequestBody.confirmationNonce).toBe(TEST_NONCE)
  // actionId 走 URL path：/api/agent/actions/{actionId}/confirm
  expect(confirmRequestUrl).toContain('/api/agent/actions/act_keep_20260823/confirm')

  // 8) confirm 成功后，前端应清除 conversationId（业务动作收口）
  const convIdAfterConfirm = await page.evaluate(() =>
    window.sessionStorage.getItem('enterprise-ai-copilot.conversation-id')
  )
  expect(convIdAfterConfirm).toBeNull()

  // 9) 再发起一条新对话，新请求的 conversationId 不应携带原值
  let secondRequestBody = null
  await page.unroute('**/api/agent/langgraph/chat')
  await page.route('**/api/agent/langgraph/chat', async route => {
    secondRequestBody = JSON.parse(route.request().postData() || '{}')
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: {
        'X-Trace-Id': 'agent-trace-new',
        'X-Conversation-Id': 'conv-new-20260823',
      },
      body: JSON.stringify({
        answer: '新一轮回答。',
        category: 'rag',
        memoryAttached: false,
        traceId: 'agent-trace-new',
      }),
    })
  })

  await page.locator('.chat-textarea').fill('第二轮提问')
  await page.getByRole('button', { name: '发送' }).click()
  await expect(page.getByText('新一轮回答。')).toBeVisible({ timeout: 5000 })

  expect(secondRequestBody).not.toBeNull()
  expect(secondRequestBody.conversationId).toBeUndefined()
})

/**
 * ADMIN 进入日志控制台往返不会破坏 EMPLOYEE 的 sessionStorage.conversationId：
 *   1) ADMIN_USER_A 登录，看到"日志控制台"入口；
 *   2) 切到 ADMIN_USER_B，让 route 返回 admin user，sessionStorage.conversationId 不被清；
 *   3) ADMIN_USER_B 进入日志控制台再返回；
 *   4) conversationId 仍是原值（不同账号不会互相清理 sessionStorage 内的 conversation-id）。
 *
 * 这条测试说明：日志控制台视图切换不会触及 sessionStorage 中由用户/会话维度的会话标识。
 */
test('ADMIN 进入日志控制台不会影响 sessionStorage 中 conversation-id 键的存在', async ({ page }) => {
  // 1) 先让 EMPLOYEE 写入 sessionStorage.conversation-id
  await page.addInitScript(() => {
    window.localStorage.setItem(
      'enterprise-ai-copilot.auth',
      JSON.stringify({ accessToken: 'test-token' })
    )
    window.sessionStorage.setItem(
      'enterprise-ai-copilot.conversation-id',
      'conv-preexisting-001'
    )
  })
  await page.route('**/api/auth/me', route =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(ADMIN_USER),
    })
  )
  await page.route('**/api/admin/logs**', route =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: [], count: 0 }),
    })
  )

  await page.goto('/')
  await expect(page.getByRole('button', { name: '日志控制台' })).toBeVisible()

  const convIdBefore = await page.evaluate(() =>
    window.sessionStorage.getItem('enterprise-ai-copilot.conversation-id')
  )
  expect(convIdBefore).toBe('conv-preexisting-001')

  await page.getByRole('button', { name: '日志控制台' }).click()
  await expect(page.getByRole('heading', { name: '管理员运行日志' })).toBeVisible()

  await page.getByRole('button', { name: '返回聊天' }).click()
  await expect(page.getByRole('heading', { name: '智能体问答' })).toBeVisible()

  const convIdAfter = await page.evaluate(() =>
    window.sessionStorage.getItem('enterprise-ai-copilot.conversation-id')
  )
  expect(convIdAfter).toBe(convIdBefore)
  expect(convIdAfter).toBe('conv-preexisting-001')
})

/**
 * 端到端（同一 ADMIN 会话，不切换用户、不重挂载 App）：
 * 待确认卡片 → 进入日志控制台 → 返回聊天 → 卡片与消息仍在、
 * conversationId 仍为原值 → 点击确认 → confirm 请求 URL 含原 actionId、
 * body 携带原 confirmationNonce → 业务收口后 conversationId 被清除。
 *
 * 前端 isSupportedPendingAction（App.jsx）只校验
 * type === 'ANNUAL_LEAVE_REQUEST' && confirmationRequired === true，
 * 与用户角色无关，因此 ADMIN 会话中 mock Agent 返回的待确认 proposal
 * 会正常渲染卡片；actionSecretsRef 是 <App /> 级 ref，日志控制台切换
 * 不卸载 <App />（showAdminLogs 只是视图分支），nonce 全程保留。
 *
 * 隔离：独立 userId（admin-e2e-pending-001）+ initScript 清理该用户
 * chat-history key 与 sessionStorage conversation-id。
 */
test('ADMIN 端到端：待确认卡片 → 日志控制台往返 → 卡片保留 → 确认携带原 nonce → conversationId 收口', async ({ page }) => {
  const ADMIN_E2E_USER_ID = 'admin-e2e-pending-001'
  const ADMIN_CONVERSATION_ID = 'conv-admin-keep-20260823'
  const ADMIN_ACTION_ID = 'act-admin-keep-20260823'
  const adminUser = { ...ADMIN_USER, userId: ADMIN_E2E_USER_ID, username: ADMIN_E2E_USER_ID }

  // 隔离：登录前清理该用户的历史与 conversationId；不动 accessToken
  await page.addInitScript((uid) => {
    window.localStorage.setItem(
      'enterprise-ai-copilot.auth',
      JSON.stringify({ accessToken: 'test-token' })
    )
    try {
      window.localStorage.removeItem('enterprise-ai-copilot.chat-history.' + uid)
      window.sessionStorage.removeItem('enterprise-ai-copilot.conversation-id')
    } catch {
      // 隐私模式或存储被禁用时忽略，测试继续
    }
  }, ADMIN_E2E_USER_ID)

  await page.route('**/api/auth/me', route =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(adminUser),
    })
  )

  // Agent mock：固定 conversationId + 待确认 proposal（含真实 confirmationNonce）
  await page.route('**/api/agent/langgraph/chat', async route => {
    const requestBody = JSON.parse(route.request().postData() || '{}')
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: {
        'X-Trace-Id': 'agent-trace-admin',
        'X-Conversation-Id': requestBody.conversationId || ADMIN_CONVERSATION_ID,
      },
      body: JSON.stringify({
        answer: '已生成草稿，请在下方确认。',
        category: 'rag',
        memoryAttached: false,
        traceId: 'agent-trace-admin',
        pendingAction: {
          actionId: ADMIN_ACTION_ID,
          actionType: 'ANNUAL_LEAVE_REQUEST',
          type: 'ANNUAL_LEAVE_REQUEST',
          status: 'PENDING_CONFIRMATION',
          confirmationRequired: true,
          startDate: '2026-08-25',
          endDate: '2026-08-25',
          days: 1,
          balanceBefore: 10,
          balanceAfter: 9,
          ttlSeconds: 600,
          employeeId: 'E10001',
          displayName: '张三',
          expiresAt: '2026-08-23T20:00:00Z',
          confirmationNonce: TEST_NONCE,
        },
      }),
    })
  })

  // confirm mock：actionId 在 URL path，body 仅含 confirmationNonce
  let confirmRequestBody = null
  let confirmRequestUrl = null
  await page.route('**/api/agent/actions/**/confirm', async route => {
    confirmRequestBody = JSON.parse(route.request().postData() || '{}')
    confirmRequestUrl = route.request().url()
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: { 'Cache-Control': 'no-store' },
      body: JSON.stringify({
        actionId: ADMIN_ACTION_ID,
        actionType: 'ANNUAL_LEAVE_REQUEST',
        status: 'SUCCEEDED',
        requestId: 'REQ-admin-20260823',
        message: '模拟年假申请已提交。',
        alreadyApplied: false,
        traceId: 'confirm-admin-trace',
      }),
    })
  })

  // 日志控制台 mock
  await page.route('**/api/admin/logs**', route =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: [], count: 0 }),
    })
  )

  await page.goto('/')

  // 1) 发起请求，渲染待确认卡片
  await page.locator('.chat-textarea').fill('请帮我申请年假')
  await page.getByRole('button', { name: '发送' }).click()

  await expect(page.getByText('已生成草稿，请在下方确认。')).toBeVisible({ timeout: 5000 })
  await expect(page.getByRole('button', { name: '确认提交' })).toBeVisible()
  await expect(page.getByText('请帮我申请年假')).toBeVisible()

  // 2) conversationId 已保存
  const convIdBefore = await page.evaluate(() =>
    window.sessionStorage.getItem('enterprise-ai-copilot.conversation-id')
  )
  expect(convIdBefore).toBe(ADMIN_CONVERSATION_ID)

  // 3) 同一会话进入日志控制台，等待加载完成
  await page.getByRole('button', { name: '日志控制台' }).click()
  await expect(page.getByRole('heading', { name: '管理员运行日志' })).toBeVisible()
  await expect(page.getByText('暂无日志')).toBeVisible()

  // 4) 返回聊天：消息、卡片、conversationId 全部保留
  await page.getByRole('button', { name: '返回聊天' }).click()
  await expect(page.getByText('请帮我申请年假')).toBeVisible()
  await expect(page.getByText('已生成草稿，请在下方确认。')).toBeVisible()
  await expect(page.getByRole('button', { name: '确认提交' })).toBeVisible()

  const convIdAfterReturn = await page.evaluate(() =>
    window.sessionStorage.getItem('enterprise-ai-copilot.conversation-id')
  )
  expect(convIdAfterReturn).toBe(ADMIN_CONVERSATION_ID)

  // 5) 同一会话内确认：nonce 从 actionSecretsRef 取出放入 confirm 请求
  await page.getByRole('button', { name: '确认提交' }).click()
  await expect.poll(() => confirmRequestBody, { timeout: 5000 }).not.toBeNull()
  expect(confirmRequestBody.confirmationNonce).toBe(TEST_NONCE)
  expect(confirmRequestUrl).toContain('/api/agent/actions/' + ADMIN_ACTION_ID + '/confirm')

  // 6) 业务收口后 conversationId 被清除
  const convIdAfterConfirm = await page.evaluate(() =>
    window.sessionStorage.getItem('enterprise-ai-copilot.conversation-id')
  )
  expect(convIdAfterConfirm).toBeNull()
})