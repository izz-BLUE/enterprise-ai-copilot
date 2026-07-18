import { expect, test } from '@playwright/test'

const TEST_NONCE = 'test-nonce-not-a-secret'
const TEST_ACTION_ID = 'act_business_action_test_1234567890'

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
  expect(captured.headers['idempotency-key']).toBeUndefined()
  expect(captured.body).toEqual({ confirmationNonce: TEST_NONCE })
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
