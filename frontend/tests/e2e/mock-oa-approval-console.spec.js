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

async function loginAs(page, user) {
  await page.addInitScript(() => {
    window.localStorage.setItem(
      'enterprise-ai-copilot.auth',
      JSON.stringify({ authenticated: true }),
    )
  })
  await page.route('**/api/auth/me', route =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(user),
    }),
  )
}

const records = [
  {
    requestId: 'OA-EXP-PENDING',
    status: 'PENDING',
    expenseId: 'EXP-PENDING',
    employeeId: 'E10001',
    tripId: 'TRIP-001',
    costCenter: 'COST-DEFAULT',
    claimedAmount: '1830.00',
    reimbursableAmount: '1730.00',
    createdAt: '2026-08-31T08:00:00Z',
  },
  {
    requestId: 'OA-EXP-APPROVED',
    status: 'APPROVED',
    expenseId: 'EXP-APPROVED',
    employeeId: 'E10002',
    tripId: 'TRIP-002',
    costCenter: 'COST-HR',
    claimedAmount: '200.00',
    reimbursableAmount: '200.00',
    createdAt: '2026-08-30T08:00:00Z',
  },
  {
    requestId: 'OA-EXP-REJECTED',
    status: 'REJECTED',
    expenseId: 'EXP-REJECTED',
    employeeId: 'E10003',
    tripId: 'TRIP-003',
    costCenter: 'COST-IT',
    claimedAmount: '80.00',
    reimbursableAmount: '60.00',
    createdAt: '2026-08-29T08:00:00Z',
  },
]

test('EMPLOYEE 看不到模拟 OA 审批入口', async ({ page }) => {
  await loginAs(page, EMPLOYEE_USER)
  await page.goto('/')

  await expect(page.getByRole('button', { name: '模拟 OA 审批' })).toHaveCount(0)
})

test('ADMIN 可以查看列表，终态不显示操作按钮', async ({ page }) => {
  await loginAs(page, ADMIN_USER)
  await page.route('**/api/admin/mock-oa/expense-approvals**', route =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: records, count: records.length }),
    }),
  )

  await page.goto('/')
  await page.getByRole('button', { name: '模拟 OA 审批' }).click()

  await expect(page.getByLabel('模拟 OA 审批台').getByRole('heading', { name: '模拟 OA 审批' })).toBeVisible()
  await expect(page.getByText('报销单：EXP-PENDING')).toBeVisible()
  await expect(page.getByText('¥1,830.00')).toBeVisible()
  await expect(page.getByText('待审批')).toBeVisible()
  await expect(page.getByText('已批准', { exact: true })).toBeVisible()
  await expect(page.getByText('已拒绝')).toBeVisible()
  await expect(page.getByRole('button', { name: '批准 OA-EXP-APPROVED' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '拒绝 OA-EXP-REJECTED' })).toHaveCount(0)
})

test('ADMIN 批准后显示 Mock OA 结果并刷新列表，浏览器不发送 Admin Token', async ({ page }) => {
  await loginAs(page, ADMIN_USER)
  let currentRecords = [records[0]]
  const requests = []
  await page.on('request', request => {
    if (request.url().includes('/api/admin/mock-oa/expense-approvals')) requests.push(request)
  })
  await page.route('**/api/admin/mock-oa/expense-approvals**', async route => {
    if (route.request().method() === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ items: currentRecords, count: currentRecords.length }),
      })
      return
    }
    currentRecords = [{ ...records[0], status: 'APPROVED' }]
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ requestId: 'OA-EXP-PENDING', status: 'APPROVED' }),
    })
  })

  await page.goto('/')
  await page.getByRole('button', { name: '模拟 OA 审批' }).click()
  await expect(page.getByRole('button', { name: '批准 OA-EXP-PENDING' })).toBeVisible()
  await page.getByRole('button', { name: '批准 OA-EXP-PENDING' }).click()

  await expect(page.getByText('Mock OA 已批准，该结果将通过 webhook 异步同步到业务系统。')).toBeVisible()
  await expect(page.getByText('已批准', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '批准 OA-EXP-PENDING' })).toHaveCount(0)
  expect(requests.some(request => request.method() === 'POST')).toBe(true)
  for (const request of requests) {
    expect(request.headers()['x-admin-token']).toBeUndefined()
  }
})

test('ADMIN 拒绝后显示 Mock OA 结果并隐藏操作按钮', async ({ page }) => {
  await loginAs(page, ADMIN_USER)
  let currentRecords = [records[0]]
  await page.route('**/api/admin/mock-oa/expense-approvals**', async route => {
    if (route.request().method() === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ items: currentRecords, count: currentRecords.length }),
      })
      return
    }
    currentRecords = [{ ...records[0], status: 'REJECTED' }]
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ requestId: 'OA-EXP-PENDING', status: 'REJECTED' }),
    })
  })

  await page.goto('/')
  await page.getByRole('button', { name: '模拟 OA 审批' }).click()
  await page.getByRole('button', { name: '拒绝 OA-EXP-PENDING' }).click()

  await expect(page.getByText('Mock OA 已拒绝，该结果将通过 webhook 异步同步到业务系统。')).toBeVisible()
  await expect(page.getByText('已拒绝', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '拒绝 OA-EXP-PENDING' })).toHaveCount(0)
})

test('审批操作处理中会禁用按钮，防止重复提交', async ({ page }) => {
  await loginAs(page, ADMIN_USER)
  let postCount = 0
  await page.route('**/api/admin/mock-oa/expense-approvals**', async route => {
    if (route.request().method() === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ items: [records[0]], count: 1 }),
      })
      return
    }
    postCount += 1
    await new Promise(resolve => setTimeout(resolve, 150))
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ requestId: records[0].requestId, status: 'APPROVED' }),
    })
  })

  await page.goto('/')
  await page.getByRole('button', { name: '模拟 OA 审批' }).click()
  const approve = page.getByRole('button', { name: '批准 OA-EXP-PENDING' })
  const clickPromise = approve.click()
  await expect(approve).toBeDisabled()
  await expect.poll(() => postCount).toBe(1)
  await clickPromise
})

test('审批台 403 显示无管理员权限', async ({ page }) => {
  await loginAs(page, ADMIN_USER)
  await page.route('**/api/admin/mock-oa/expense-approvals**', route =>
    route.fulfill({
      status: 403,
      contentType: 'application/json',
      body: JSON.stringify({ errorCode: 'FORBIDDEN', message: '无权访问该资源。' }),
    }),
  )

  await page.goto('/')
  await page.getByRole('button', { name: '模拟 OA 审批' }).click()

  await expect(page.getByText('无管理员权限，无法访问模拟 OA 审批。')).toBeVisible()
})

test('审批台超时提示结果未知', async ({ page }) => {
  await loginAs(page, ADMIN_USER)
  await page.route('**/api/admin/mock-oa/expense-approvals**', route =>
    route.fulfill({
      status: 503,
      contentType: 'application/json',
      body: JSON.stringify({ errorCode: 'MOCK_OA_TIMEOUT', message: '结果未知' }),
    }),
  )

  await page.goto('/')
  await page.getByRole('button', { name: '模拟 OA 审批' }).click()

  await expect(page.getByText('模拟 OA 请求超时，结果未知，请刷新列表确认状态。')).toBeVisible()
})
