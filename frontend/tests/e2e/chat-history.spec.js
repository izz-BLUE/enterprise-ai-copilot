import { expect, test } from '@playwright/test'

// 用户会话历史恢复 (按 用户 + 模式 隔离) 端到端测试。
// mock 身份结构与真实 /api/auth/login 响应完全一致:
// { accessToken, tokenType, expiresIn, user: { userId, username, employeeId, displayName, role, enabled } }
// 测试全部走真实登录表单与"退出登录"按钮流程 + page.reload()。
// 产品规则:
//   - agent / rag 历史各自独立保存与恢复,切换互不覆盖、互不删除;
//   - 日志控制台是纯视图分支,进出一律不触碰任何历史;
//   - 仅"清空会话"删除当前模式历史;退出登录只清认证态。
const ADMIN_USER = {
  userId: 'U90001',
  username: 'admin',
  employeeId: null,
  displayName: '管理员',
  role: 'ADMIN',
  enabled: true,
}

const ZHANGSAN_USER = {
  userId: 'U10001',
  username: 'zhangsan',
  employeeId: 'E10001',
  displayName: '张三',
  role: 'EMPLOYEE',
  enabled: true,
}

const TEST_CONVERSATION_ID = 'conv-history-e2e-001'
const ANSWER_TEXT = '这是用于验证历史恢复的测试回答(唯一标识)。'
const RAG_ANSWER_TEXT = '这是用于验证标准问答历史恢复的测试回答(RAG 标识)。'

const historyBase = userId => `enterprise-ai-copilot.chat-history.${userId}`
const historyKeyOf = (userId, mode) => `${historyBase(userId)}.${mode}`

async function mockAuth(page, user) {
  let currentUser = user
  await page.route('**/api/auth/login', route =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        accessToken: 'test-token',
        tokenType: 'Bearer',
        expiresIn: 3600,
        user: currentUser,
      }),
    })
  )
  await page.route('**/api/auth/me', route =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(currentUser),
    })
  )
  return { setUser: nextUser => { currentUser = nextUser } }
}

// 与真实 Java 行为一致:agent 路由响应头 X-Conversation-Id 是权威会话 ID。
// requests 记录每次请求体,供断言"恢复的 conversationId 被带回"。
async function mockAgentChat(page, requests) {
  await page.route('**/api/agent/langgraph/chat', async route => {
    const requestBody = JSON.parse(route.request().postData() || '{}')
    requests.push(requestBody)
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: {
        'X-Trace-Id': 'trace-history-e2e',
        'X-Conversation-Id': requestBody.conversationId || TEST_CONVERSATION_ID,
      },
      body: JSON.stringify({
        answer: ANSWER_TEXT,
        model: 'deepseek',
        traceId: 'trace-history-e2e',
        route: 'rag',
        safe: true,
        category: 'normal',
        reason: '',
        sources: [],
        success: true,
      }),
    })
  })
}

// 普通 RAG 路由 (/api/chat) 不参与 conversationId 链路,与真实行为一致。
async function mockRagChat(page, requests) {
  await page.route('**/api/chat', async route => {
    const requestBody = JSON.parse(route.request().postData() || '{}')
    requests.push(requestBody)
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        answer: RAG_ANSWER_TEXT,
        traceId: 'trace-rag-e2e',
        route: 'rag',
        safe: true,
        category: 'normal',
        sources: [],
        success: true,
      }),
    })
  })
}

async function loginViaForm(page, username) {
  await page.goto('/')
  await page.getByRole('textbox', { name: '用户名' }).fill(username)
  await page.getByRole('textbox', { name: '密码' }).fill('demo123456')
  await page.getByRole('button', { name: '登录' }).click()
  await expect(page.getByRole('heading', { name: '智能体问答' })).toBeVisible()
}

async function ask(page, question, expectedAnswer) {
  const input = page.getByPlaceholder(/输入问题/)
  await input.fill(question)
  await input.press('Enter')
  await expect(page.locator('.messages-list')).toContainText(question)
  await expect(page.locator('.messages-list .markdown-body').last()).toContainText(expectedAnswer)
}

async function switchMode(page, modeLabel) {
  await page.getByRole('button', { name: modeLabel }).click()
}

async function logoutViaButton(page) {
  await page.getByRole('button', { name: '退出登录' }).click()
  await expect(page.getByRole('heading', { name: '登录工作台' })).toBeVisible()
}

const readHistoryRecord = (page, userId, mode) => page.evaluate(key => {
  const raw = localStorage.getItem(key)
  if (raw === null) return null
  return JSON.parse(raw)
}, historyKeyOf(userId, mode))

const readLegacyRecord = (page, userId) => page.evaluate(key => {
  const raw = localStorage.getItem(key)
  if (raw === null) return null
  return JSON.parse(raw)
}, historyBase(userId))

const readSessionConversationId = page => page.evaluate(
  () => sessionStorage.getItem('enterprise-ai-copilot.conversation-id')
)

test('agent 发送消息 → 日志控制台 → 智能体问答:消息/conversationId 保留,敏感字段不入盘,刷新后恢复', async ({ page }) => {
  const requests = []
  await mockAuth(page, ADMIN_USER)
  await mockAgentChat(page, requests)
  await page.route('**/api/admin/logs**', route =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: [], count: 0 }),
    })
  )
  await loginViaForm(page, 'admin')

  const question = 'HIST-E2E-LOGTAB-001 请介绍请假流程'
  await ask(page, question, ANSWER_TEXT)

  // 落盘：agent 模式独立 key,2 条消息,无任何敏感字段
  const recordBefore = await readHistoryRecord(page, ADMIN_USER.userId, 'agent')
  expect(recordBefore).not.toBeNull()
  expect(recordBefore.messages).toHaveLength(2)
  expect(recordBefore.conversationId).toBe(TEST_CONVERSATION_ID)
  const rawBefore = JSON.stringify(recordBefore)
  expect(rawBefore).not.toContain('confirmationNonce')
  expect(rawBefore).not.toContain('test-token')
  expect(rawBefore).not.toMatch(/Bearer|Authorization|accessToken|Idempotency|Scope|taskState/i)
  expect(await readSessionConversationId(page)).toBe(TEST_CONVERSATION_ID)

  // 打开日志控制台:localStorage 与 sessionStorage 一律不动
  await page.getByRole('button', { name: '日志控制台' }).click()
  await expect(page.getByRole('heading', { name: '管理员运行日志' })).toBeVisible()
  const recordInConsole = await readHistoryRecord(page, ADMIN_USER.userId, 'agent')
  expect(recordInConsole.messages).toHaveLength(2)
  expect(await readSessionConversationId(page)).toBe(TEST_CONVERSATION_ID)

  // 控制台内点击"智能体问答":退出控制台,原消息仍存在
  await switchMode(page, '切换到智能体问答')
  await expect(page.getByRole('heading', { name: '智能体问答' })).toBeVisible()
  await expect(page.locator('.messages-list')).toContainText(question)
  await expect(page.locator('.messages-list').getByText(question)).toHaveCount(1)
  expect(await readSessionConversationId(page)).toBe(TEST_CONVERSATION_ID)

  // 刷新:当前模式(agent)历史完整恢复
  await page.reload()
  await expect(page.getByRole('heading', { name: '智能体问答' })).toBeVisible()
  await expect(page.locator('.messages-list')).toContainText(question)
  await expect(page.locator('.messages-list .markdown-body')).toContainText(ANSWER_TEXT)
  const recordAfter = await readHistoryRecord(page, ADMIN_USER.userId, 'agent')
  expect(recordAfter.messages).toHaveLength(2)
})

test('agent → 标准问答 → 智能体问答:agent 历史恢复且未被覆盖,conversationId 按模式隔离', async ({ page }) => {
  const agentRequests = []
  await mockAuth(page, ADMIN_USER)
  await mockAgentChat(page, agentRequests)
  await loginViaForm(page, 'admin')

  const agentQuestion = 'HIST-E2E-SWITCH-001 agent 消息'
  await ask(page, agentQuestion, ANSWER_TEXT)
  expect(await readSessionConversationId(page)).toBe(TEST_CONVERSATION_ID)

  // 切到 rag:agent 历史保留,rag 无历史展示空聊天,conversationId 运行时空
  await switchMode(page, '切换到标准问答')
  await expect(page.getByRole('heading', { name: '标准问答' })).toBeVisible()
  await expect(page.getByText(agentQuestion)).toHaveCount(0)
  const agentRecordAfterSwitch = await readHistoryRecord(page, ADMIN_USER.userId, 'agent')
  expect(agentRecordAfterSwitch.messages).toHaveLength(2)
  expect(agentRecordAfterSwitch.conversationId).toBe(TEST_CONVERSATION_ID)
  expect(await readSessionConversationId(page)).toBeNull()

  // 切回 agent:历史恢复,原对话消息不重复
  await switchMode(page, '切换到智能体问答')
  await expect(page.getByRole('heading', { name: '智能体问答' })).toBeVisible()
  await expect(page.locator('.messages-list')).toContainText(agentQuestion)
  await expect(page.locator('.messages-list').getByText(agentQuestion)).toHaveCount(1)
  expect(await readSessionConversationId(page)).toBe(TEST_CONVERSATION_ID)
  // 恢复的 conversationId 被下一次 agent 请求带回
  await ask(page, 'HIST-E2E-SWITCH-002 agent 第二条', ANSWER_TEXT)
  expect(agentRequests).toHaveLength(2)
  expect(agentRequests[1].conversationId).toBe(TEST_CONVERSATION_ID)
})

test('agent / rag 各自消息并存:来回切换只显示对应模式,清空 rag 不影响 agent', async ({ page }) => {
  await mockAuth(page, ADMIN_USER)
  await mockAgentChat(page, [])
  await mockRagChat(page, [])
  await loginViaForm(page, 'admin')

  const agentQuestion = 'HIST-E2E-SPLIT-AGENT-001'
  const ragQuestion = 'HIST-E2E-SPLIT-RAG-001'
  await ask(page, agentQuestion, ANSWER_TEXT)
  await switchMode(page, '切换到标准问答')
  await ask(page, ragQuestion, RAG_ANSWER_TEXT)
  expect(await readHistoryRecord(page, ADMIN_USER.userId, 'agent')).not.toBeNull()
  expect(await readHistoryRecord(page, ADMIN_USER.userId, 'rag')).not.toBeNull()

  // 切回 agent:只看到 agent 消息
  await switchMode(page, '切换到智能体问答')
  await expect(page.locator('.messages-list')).toContainText(agentQuestion)
  await expect(page.getByText(ragQuestion)).toHaveCount(0)
  // 再切回 rag:只看到 rag 消息
  await switchMode(page, '切换到标准问答')
  await expect(page.locator('.messages-list')).toContainText(ragQuestion)
  await expect(page.getByText(agentQuestion)).toHaveCount(0)

  // 清空会话(rag 模式):仅删 .rag 历史,agent 历史不受影响
  await page.getByRole('button', { name: '清空会话' }).click()
  await page.getByRole('button', { name: '确认清空会话' }).click()
  await expect(page.getByRole('heading', { name: '标准问答' })).toBeVisible()
  expect(await readHistoryRecord(page, ADMIN_USER.userId, 'rag')).toBeNull()
  expect(await readHistoryRecord(page, ADMIN_USER.userId, 'agent')).not.toBeNull()

  // 切回 agent:agent 消息仍在
  await switchMode(page, '切换到智能体问答')
  await expect(page.locator('.messages-list')).toContainText(agentQuestion)
})

test('agent 有历史 → 日志控制台 → 标准问答 → 智能体问答:agent 历史仍在', async ({ page }) => {
  await mockAuth(page, ADMIN_USER)
  await mockAgentChat(page, [])
  await page.route('**/api/admin/logs**', route =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: [], count: 0 }),
    })
  )
  await loginViaForm(page, 'admin')

  const agentQuestion = 'HIST-E2E-GOOD-ROUTE-001'
  await ask(page, agentQuestion, ANSWER_TEXT)

  // 日志控制台 → 标准问答 → 智能体问答(真实路径)
  await page.getByRole('button', { name: '日志控制台' }).click()
  await expect(page.getByRole('heading', { name: '管理员运行日志' })).toBeVisible()
  await switchMode(page, '切换到标准问答')
  await expect(page.getByRole('heading', { name: '标准问答' })).toBeVisible()
  await switchMode(page, '切换到智能体问答')
  await expect(page.getByRole('heading', { name: '智能体问答' })).toBeVisible()
  await expect(page.locator('.messages-list')).toContainText(agentQuestion)
  expect(await readSessionConversationId(page)).toBe(TEST_CONVERSATION_ID)
})

test('rag 模式刷新:恢复上次模式(标准问答)及其 rag 历史', async ({ page }) => {
  await mockAuth(page, ADMIN_USER)
  await mockRagChat(page, [])
  await loginViaForm(page, 'admin')

  await switchMode(page, '切换到标准问答')
  const ragQuestion = 'HIST-E2E-RAG-RELOAD-001'
  await ask(page, ragQuestion, RAG_ANSWER_TEXT)

  await page.reload()
  // 模式持久化:刷新后仍停留在标准问答,rag 历史恢复
  await expect(page.getByRole('heading', { name: '标准问答' })).toBeVisible()
  await expect(page.locator('.messages-list')).toContainText(ragQuestion)
  await expect(page.locator('.messages-list .markdown-body')).toContainText(RAG_ANSWER_TEXT)
  // rag 不参与 conversationId 链路
  expect(await readSessionConversationId(page)).toBeNull()
})

test('admin 退出重登:agent 与 rag 历史分别恢复,认证态被清而历史保留', async ({ page }) => {
  await mockAuth(page, ADMIN_USER)
  await mockAgentChat(page, [])
  await mockRagChat(page, [])
  await loginViaForm(page, 'admin')

  const agentQuestion = 'HIST-E2E-RELOGIN-AGENT-001'
  await ask(page, agentQuestion, ANSWER_TEXT)
  await switchMode(page, '切换到标准问答')
  const ragQuestion = 'HIST-E2E-RELOGIN-RAG-001'
  await ask(page, ragQuestion, RAG_ANSWER_TEXT)

  // 退出:清认证态,agent/rag 历史均保留
  await logoutViaButton(page)
  expect(await page.evaluate(() => localStorage.getItem('enterprise-ai-copilot.auth'))).toBeNull()
  expect(await readSessionConversationId(page)).toBeNull()
  expect((await readHistoryRecord(page, ADMIN_USER.userId, 'agent')).messages).toHaveLength(2)
  expect((await readHistoryRecord(page, ADMIN_USER.userId, 'rag')).messages).toHaveLength(2)

  // 同一账号重登:恢复上次所在模式(rag),再切 agent 恢复 agent 历史
  await page.getByRole('textbox', { name: '用户名' }).fill('admin')
  await page.getByRole('textbox', { name: '密码' }).fill('demo123456')
  await page.getByRole('button', { name: '登录' }).click()
  await expect(page.getByRole('heading', { name: '标准问答' })).toBeVisible()
  await expect(page.locator('.messages-list')).toContainText(ragQuestion)
  await switchMode(page, '切换到智能体问答')
  await expect(page.locator('.messages-list')).toContainText(agentQuestion)
  expect(await readSessionConversationId(page)).toBe(TEST_CONVERSATION_ID)
})

test('zhangsan 登录:看不到 admin 任一模式历史(用户隔离)', async ({ page }) => {
  const auth = await mockAuth(page, ADMIN_USER)
  await mockAgentChat(page, [])
  await mockRagChat(page, [])
  await loginViaForm(page, 'admin')

  const adminAgentQuestion = 'HIST-E2E-ISO-ADMIN-AGENT-001'
  await ask(page, adminAgentQuestion, ANSWER_TEXT)
  await switchMode(page, '切换到标准问答')
  const adminRagQuestion = 'HIST-E2E-ISO-ADMIN-RAG-001'
  await ask(page, adminRagQuestion, RAG_ANSWER_TEXT)
  await logoutViaButton(page)

  // zhangsan 登录:两个模式都看不到 admin 消息
  auth.setUser(ZHANGSAN_USER)
  await page.getByRole('textbox', { name: '用户名' }).fill('zhangsan')
  await page.getByRole('textbox', { name: '密码' }).fill('demo123456')
  await page.getByRole('button', { name: '登录' }).click()
  await expect(page.getByRole('heading', { name: '智能体问答' })).toBeVisible()
  await expect(page.getByText(adminAgentQuestion)).toHaveCount(0)
  await switchMode(page, '切换到标准问答')
  await expect(page.getByText(adminRagQuestion)).toHaveCount(0)
  // admin 的两个模式历史仍独立存在于 admin 的 key 下
  expect((await readHistoryRecord(page, ADMIN_USER.userId, 'agent')).messages).toHaveLength(2)
  expect((await readHistoryRecord(page, ADMIN_USER.userId, 'rag')).messages).toHaveLength(2)
  expect(await readHistoryRecord(page, ZHANGSAN_USER.userId, 'agent')).toBeNull()
  expect(await readHistoryRecord(page, ZHANGSAN_USER.userId, 'rag')).toBeNull()
})

test('旧格式历史兼容迁移:无模式后缀的历史按默认 agent 迁移,消息不丢失', async ({ page }) => {
  // 预置旧格式记录(升级前的单一历史快照,业务动作发生在 agent 模式)
  const legacyRecord = {
    conversationId: 'conv-legacy-0001',
    messages: [
      { type: 'user', id: 'legacy-u-1', question: 'HIST-E2E-LEGACY-001 旧格式消息' },
      {
        type: 'assistant',
        id: 'legacy-a-1',
        question: 'HIST-E2E-LEGACY-001 旧格式消息',
        result: { success: true, answer: ANSWER_TEXT, route: 'rag' },
        resultMode: 'agent',
        actionUi: null,
      },
    ],
    updatedAt: Date.now(),
  }
  await page.addInitScript(({ authKey, legacyKey, record }) => {
    window.localStorage.setItem(authKey, JSON.stringify({ authenticated: true }))
    window.localStorage.setItem(legacyKey, JSON.stringify(record))
  }, {
    authKey: 'enterprise-ai-copilot.auth',
    legacyKey: historyBase(ZHANGSAN_USER.userId),
    record: legacyRecord,
  })
  await mockAuth(page, ZHANGSAN_USER)
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '智能体问答' })).toBeVisible()

  // agent 模式恢复旧格式消息
  await expect(page.locator('.messages-list')).toContainText('HIST-E2E-LEGACY-001 旧格式消息')
  await expect(page.locator('.messages-list .markdown-body')).toContainText(ANSWER_TEXT)
  // 迁移完成:.agent key 已有完整数据,旧 key 移除(数据不丢失)
  const migrated = await readHistoryRecord(page, ZHANGSAN_USER.userId, 'agent')
  expect(migrated.messages).toHaveLength(2)
  expect(migrated.conversationId).toBe('conv-legacy-0001')
  expect(await readLegacyRecord(page, ZHANGSAN_USER.userId)).toBeNull()
})
