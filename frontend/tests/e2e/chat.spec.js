import { expect, test } from '@playwright/test'

const agentResponse = (overrides = {}) => ({
  answer: '根据企业知识库，标准上班时间为上午 9:30。',
  model: 'deepseek',
  traceId: '11111111-1111-4111-8111-111111111111',
  route: 'rag',
  safe: true,
  category: 'normal',
  reason: '',
  sources: ['hr_leave_policy'],
  success: true,
  ...overrides,
})

async function mockAgent(page, response) {
  await page.route('**/api/agent/langgraph/chat', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(response),
    })
  })
}

async function ask(page, question) {
  const input = page.getByPlaceholder('输入问题... (Enter 发送，Shift+Enter 换行)')
  await input.fill(question)
  await input.press('Enter')
}

test('loads the chat workspace', async ({ page }) => {
  await page.goto('/')

  await expect(page.getByRole('heading', { name: '智能体问答' })).toBeVisible()
  await expect(page.getByText('欢迎使用 Enterprise AI Copilot')).toBeVisible()
  await expect(page.getByRole('button', { name: /发送/ })).toBeDisabled()
})

test('renders a representative knowledge answer', async ({ page }) => {
  await mockAgent(page, agentResponse())
  await page.goto('/')
  await ask(page, '几点上班？')

  await expect(page.getByText('上午 9:30')).toBeVisible()
  await expect(page.getByText('请求成功')).toBeVisible()
  await expect(page.getByText('RAG 问答')).toBeVisible()
})

test('preserves a single tilde while supporting GFM deletion', async ({ page }) => {
  await mockAgent(page, agentResponse({
    answer: '工作时间：9:30~12:30，~~旧时间~~。',
  }))
  await page.goto('/')
  await ask(page, '工作时间')

  const message = page.locator('.markdown-body')
  await expect(message).toContainText('9:30~12:30')
  await expect(message.locator('del')).toHaveText('旧时间')
})

test('shows deterministic safety refusal', async ({ page }) => {
  await mockAgent(page, agentResponse({
    answer: '抱歉，我不能协助提供违法、违规或伪造材料的方法。',
    route: 'refuse',
    safe: false,
    category: 'illegal_or_policy_violation',
    reason: '命中高风险关键词：伪造',
    sources: [],
  }))
  await page.goto('/')
  await ask(page, '怎么伪造病假证明？')

  await expect(page.getByText('安全拒答', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('安全拦截')).toBeVisible()
  await expect(page.getByText(/不能协助提供违法/)).toBeVisible()
})

test('keeps the input visible and scrolls long answers with the mouse wheel', async ({ page }) => {
  const longAnswer = Array.from(
    { length: 32 },
    (_, index) => `${index + 1}. 这是用于验证消息区域滚动行为的知识库回答。`,
  ).join('\n\n')

  await mockAgent(page, agentResponse({ answer: longAnswer }))
  await page.goto('/')
  await ask(page, '返回长回答')

  const chatArea = page.locator('.chat-area')
  const input = page.getByPlaceholder('输入问题... (Enter 发送，Shift+Enter 换行)')
  await expect(input).toBeVisible()

  await expect.poll(async () => chatArea.evaluate(el => el.scrollHeight > el.clientHeight)).toBe(true)
  await expect.poll(async () => chatArea.evaluate(el => el.scrollTop)).toBeGreaterThan(0)
  const before = await chatArea.evaluate(el => el.scrollTop)
  const box = await chatArea.boundingBox()
  expect(box).not.toBeNull()
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.wheel(0, -600)
  await expect.poll(async () => chatArea.evaluate(el => el.scrollTop)).toBeLessThan(before)
  await expect(input).toBeVisible()
})
