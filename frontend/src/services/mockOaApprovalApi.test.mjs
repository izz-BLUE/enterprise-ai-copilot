import assert from 'node:assert/strict'
import test from 'node:test'
import {
  decideMockOaApproval,
  listMockOaApprovals,
  MockOaApprovalApiError,
} from './mockOaApprovalApi.js'

test('审批列表请求只携带认证请求头并支持状态筛选', async () => {
  const originalFetch = globalThis.fetch
  let captured
  globalThis.fetch = async (url, options) => {
    captured = { url, options }
    return new Response(JSON.stringify({ items: [], count: 0 }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }
  try {
    await listMockOaApprovals({ accessToken: 'jwt-token', status: 'PENDING' })
  } finally {
    globalThis.fetch = originalFetch
  }

  assert.equal(captured.url, '/api/admin/mock-oa/expense-approvals?status=PENDING')
  assert.equal(captured.options.headers.get('Authorization'), 'Bearer jwt-token')
  assert.equal(captured.options.headers.get('X-Admin-Token'), null)
})

test('审批操作使用 Java Admin API 的批准路径', async () => {
  const originalFetch = globalThis.fetch
  let captured
  globalThis.fetch = async (url, options) => {
    captured = { url, options }
    return new Response(JSON.stringify({ requestId: 'OA-EXP-1', status: 'APPROVED' }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }
  try {
    await decideMockOaApproval({
      accessToken: 'jwt-token',
      requestId: 'OA-EXP-1',
      decision: 'APPROVED',
    })
  } finally {
    globalThis.fetch = originalFetch
  }

  assert.equal(captured.url, '/api/admin/mock-oa/expense-approvals/OA-EXP-1/approve')
  assert.equal(captured.options.method, 'POST')
  assert.equal(captured.options.headers.get('X-Admin-Token'), null)
})

test('403 和 Mock OA 超时返回明确的安全错误', async () => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = async () => new Response(JSON.stringify({ errorCode: 'FORBIDDEN' }), {
    status: 403,
    headers: { 'Content-Type': 'application/json' },
  })
  try {
    await assert.rejects(
      () => listMockOaApprovals({ accessToken: 'jwt-token' }),
      error => error instanceof MockOaApprovalApiError
        && error.httpStatus === 403
        && error.message.includes('无管理员权限'),
    )
  } finally {
    globalThis.fetch = originalFetch
  }
})
