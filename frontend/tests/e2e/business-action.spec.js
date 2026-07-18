import { expect, test } from '@playwright/test'

const TEST_NONCE = 'test-nonce-not-a-secret'
const TEST_ACTION_ID = 'act_business_action_test_1234567890'

const demoIdentities = {
  identities: [
    { userId: 'DEMO-001', displayName: 'Demo User', role: 'EMPLOYEE' },
    { userId: 'DEMO-002', displayName: 'Demo User B', role: 'EMPLOYEE' },
    { userId: 'DEMO-MGR-001', displayName: 'Demo Manager', role: 'MANAGER' },
  ],
}

test.beforeEach(async ({ page }) => {
  await page.route('**/api/demo/identities', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    headers: { 'Cache-Control': 'no-store' },
    body: JSON.stringify(demoIdentities),
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

test('身份选择器加载3个身份并默认选择User A', async ({ page }) => {
  await page.goto('/')

  const selector = page.getByRole('combobox', { name: '演示身份' })
  await expect(selector.locator('option')).toHaveCount(3)
  await expect(selector).toHaveValue('DEMO-001')
  await expect(page.getByText('演示身份仅用于展示数据隔离，不是真实登录。')).toBeVisible()
})

test('Agent请求只在Header携带当前身份', async ({ page }) => {
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

  expect(captured.headers['x-demo-user-id']).toBe('DEMO-001')
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
  expect(captured.headers['x-demo-user-id']).toBe('DEMO-001')
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
  expect(captured.headers['x-demo-user-id']).toBe('DEMO-001')
  expect(captured.headers['idempotency-key']).toBeUndefined()
  expect(captured.body).toEqual({ confirmationNonce: TEST_NONCE })
})

test('身份切换确认后清空会话与旧草稿', async ({ page }) => {
  await openDraft(page)
  page.once('dialog', dialog => dialog.accept())

  await page.getByRole('combobox', { name: '演示身份' }).selectOption('DEMO-002')

  await expect(page.getByRole('combobox', { name: '演示身份' })).toHaveValue('DEMO-002')
  await expect(page.getByRole('region', { name: '年假申请确认卡' })).toHaveCount(0)
  await expect(page.getByText('欢迎使用 Enterprise AI Copilot')).toBeVisible()
  await expect(page.locator('body')).not.toContainText(TEST_NONCE)
  await expect(page.locator('body')).not.toContainText(TEST_ACTION_ID)
})

test('用户取消身份切换时保留原身份与草稿', async ({ page }) => {
  await openDraft(page)
  page.once('dialog', dialog => dialog.dismiss())

  await page.getByRole('combobox', { name: '演示身份' }).selectOption('DEMO-002')

  await expect(page.getByRole('combobox', { name: '演示身份' })).toHaveValue('DEMO-001')
  await expect(page.getByRole('region', { name: '年假申请确认卡' })).toBeVisible()
})

test('Confirm执行期间身份选择器不可切换', async ({ page }) => {
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
  await expect(page.getByRole('combobox', { name: '演示身份' })).toBeDisabled()
  release()
  await click
  await expect(page.getByText('模拟申请已提交')).toBeVisible()
})

test('未知身份错误显示安全提示且不显示堆栈', async ({ page }) => {
  await page.route('**/api/agent/langgraph/chat', route => route.fulfill({
    status: 403,
    contentType: 'application/json',
    body: JSON.stringify({
      answer: '请选择有效的演示身份。',
      route: 'error',
      category: 'demo_identity',
      success: false,
      traceId: 'identity-error-trace',
    }),
  }))
  await page.goto('/')
  await askForLeave(page)

  await expect(page.getByText('请选择有效的演示身份。')).toBeVisible()
  await expect(page.locator('body')).not.toContainText(/Exception|stack trace/i)
})

test('标准RAG不要求或发送Demo身份Header', async ({ page }) => {
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
  expect(captured.headers['x-demo-user-id']).toBeUndefined()
  expect(captured.headers['x-admin-token']).toBeUndefined()
  expect(captured.body).toEqual({ message: '几点上班？' })
  expect(captured.body.userId).toBeUndefined()
  expect(captured.body.employeeId).toBeUndefined()
})

test('身份接口延迟不阻断标准RAG', async ({ page }) => {
  await page.unroute('**/api/demo/identities')
  let releaseIdentity
  let markIdentityRequested
  const identityResponseGate = new Promise(resolve => { releaseIdentity = resolve })
  const identityRequested = new Promise(resolve => { markIdentityRequested = resolve })
  await page.route('**/api/demo/identities', async route => {
    markIdentityRequested()
    await identityResponseGate
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: { 'Cache-Control': 'no-store' },
      body: JSON.stringify(demoIdentities),
    })
  })

  await page.goto('/')
  await identityRequested
  const standardRagButton = page.getByRole('button', { name: '切换到标准问答' })
  try {
    await expect(page.locator('#root')).toBeAttached()
    await expect(standardRagButton).toBeVisible()
    await expect(standardRagButton).toBeEnabled()
    await standardRagButton.click()
    await expect(standardRagButton).toHaveAttribute('aria-pressed', 'true')
  } finally {
    releaseIdentity()
  }
  await expect(page.getByRole('combobox', { name: '演示身份' })).toHaveValue('DEMO-001')
})

test('身份接口失败不阻断标准RAG请求', async ({ page }) => {
  await page.unroute('**/api/demo/identities')
  await page.route('**/api/demo/identities', route => route.fulfill({
    status: 503,
    contentType: 'application/json',
    body: JSON.stringify({ errorCode: 'SERVICE_UNAVAILABLE' }),
  }))
  let captured
  await page.route('**/api/chat', async route => {
    captured = {
      headers: route.request().headers(),
      body: route.request().postDataJSON(),
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ answer: '标准问答可用', success: true, traceId: 'rag-503-trace' }),
    })
  })

  await page.goto('/')
  await expect(page.locator('#root')).toBeAttached()
  await expect(page.getByRole('alert')).toHaveText('无法加载演示身份。')
  await page.getByRole('button', { name: '切换到标准问答' }).click()
  const input = page.getByPlaceholder('输入问题... (Enter 发送，Shift+Enter 换行)')
  await input.fill('身份服务失败时还能问答吗？')
  await input.press('Enter')

  await expect(page.getByText('标准问答可用')).toBeVisible()
  expect(captured.headers['x-demo-user-id']).toBeUndefined()
  expect(captured.headers['x-admin-token']).toBeUndefined()
  expect(captured.body).toEqual({ message: '身份服务失败时还能问答吗？' })
})

test('服务端 ACTION_EXPIRED 进入过期终态', async ({ page }) => {
  await page.route('**/api/agent/actions/**/confirm', route => route.fulfill({
    status: 409,
    contentType: 'application/json',
    body: JSON.stringify({
      actionId: TEST_ACTION_ID,
      status: 'FAILED',
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
