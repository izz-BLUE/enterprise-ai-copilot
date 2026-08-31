import { expect, test } from '@playwright/test'

test('默认填充公开 demo 账号且登录后进入工作台', async ({ page }) => {
  await page.route('**/api/auth/login', async route => {
    expect(route.request().postDataJSON()).toEqual({ username: 'demo', password: 'demo-public-2026' })
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        accessToken: 'test-token',
        tokenType: 'Bearer',
        expiresIn: 3600,
        user: {
          userId: 'U10000',
          username: 'demo',
          employeeId: 'E10000',
          displayName: '公开演示账号',
          role: 'EMPLOYEE',
          enabled: true,
        },
      }),
    })
  })

  await page.goto('/')
  await expect(page.getByLabel('用户名')).toHaveValue('demo')
  await expect(page.getByLabel('密码')).toHaveValue('demo-public-2026')
  await expect(page.getByText('账号切换')).toHaveCount(0)

  await page.getByRole('button', { name: '登录' }).click()

  await expect(page.getByRole('heading', { name: '智能体问答' })).toBeVisible()
  await expect(page.getByText('公开演示账号')).toBeVisible()
  expect(JSON.parse(await page.evaluate(() => localStorage.getItem('enterprise-ai-copilot.auth'))))
    .toEqual({ authenticated: true })
})

test('退出登录清理本地会话并返回登录页', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('enterprise-ai-copilot.auth', JSON.stringify({ authenticated: true }))
  })
  await page.route('**/api/auth/me', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ userId: 'U10001', username: 'zhangsan', employeeId: 'E10001', displayName: '张三', role: 'EMPLOYEE', enabled: true }),
  }))
  await page.route('**/api/auth/logout', async route => {
    expect(route.request().headers()['x-requested-with']).toBe('XMLHttpRequest')
    await route.fulfill({ status: 204 })
  })

  await page.goto('/')
  await page.getByRole('button', { name: '退出登录' }).click()

  await expect(page.getByRole('heading', { name: '登录工作台' })).toBeVisible()
  expect(await page.evaluate(() => localStorage.getItem('enterprise-ai-copilot.auth'))).toBeNull()
})

test('登录状态校验遇到服务端故障时保留认证信息并允许重试', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('enterprise-ai-copilot.auth', JSON.stringify({ authenticated: true }))
  })
  let attempts = 0
  let serviceRecovered = false
  await page.route('**/api/auth/me', route => {
    attempts += 1
    if (!serviceRecovered) {
      return route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({ message: 'temporarily unavailable' }),
      })
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ userId: 'U10001', username: 'zhangsan', employeeId: 'E10001', displayName: '张三', role: 'EMPLOYEE', enabled: true }),
    })
  })

  await page.goto('/')
  await expect(page.getByRole('heading', { name: '暂时无法连接服务' })).toBeVisible()
  expect(await page.evaluate(() => localStorage.getItem('enterprise-ai-copilot.auth'))).not.toBeNull()

  serviceRecovered = true
  await page.getByRole('button', { name: '重新验证' }).click()
  await expect(page.getByRole('heading', { name: '智能体问答' })).toBeVisible()
  expect(attempts).toBeGreaterThanOrEqual(2)
})
