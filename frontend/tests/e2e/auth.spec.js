import { expect, test } from '@playwright/test'

test('默认用户名为zhangsan且登录后进入工作台', async ({ page }) => {
  await page.route('**/api/auth/login', async route => {
    expect(route.request().postDataJSON()).toEqual({ username: 'zhangsan', password: 'not-in-bundle' })
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        accessToken: 'test-token',
        tokenType: 'Bearer',
        expiresIn: 3600,
        user: {
          userId: 'U10001',
          username: 'zhangsan',
          employeeId: 'E10001',
          displayName: '张三',
          role: 'EMPLOYEE',
          enabled: true,
        },
      }),
    })
  })

  await page.goto('/')
  await expect(page.getByLabel('用户名')).toHaveValue('zhangsan')
  await expect(page.getByLabel('密码')).toHaveValue('')
  await expect(page.getByText('账号切换')).toHaveCount(0)

  await page.getByLabel('密码').fill('not-in-bundle')
  await page.getByRole('button', { name: '登录' }).click()

  await expect(page.getByRole('heading', { name: '智能体问答' })).toBeVisible()
  await expect(page.getByText('张三')).toBeVisible()
  expect(JSON.parse(await page.evaluate(() => localStorage.getItem('enterprise-ai-copilot.auth'))))
    .toEqual({ accessToken: 'test-token' })
})

test('退出登录清理本地会话并返回登录页', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('enterprise-ai-copilot.auth', JSON.stringify({ accessToken: 'test-token' }))
  })
  await page.route('**/api/auth/me', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ userId: 'U10001', username: 'zhangsan', employeeId: 'E10001', displayName: '张三', role: 'EMPLOYEE', enabled: true }),
  }))

  await page.goto('/')
  await page.getByRole('button', { name: '退出登录' }).click()

  await expect(page.getByRole('heading', { name: '登录工作台' })).toBeVisible()
  expect(await page.evaluate(() => localStorage.getItem('enterprise-ai-copilot.auth'))).toBeNull()
})
