import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { readdir, readFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const scriptDirectory = dirname(fileURLToPath(import.meta.url))
const frontendDirectory = resolve(scriptDirectory, '..')
const distDirectory = resolve(frontendDirectory, 'dist')
const port = 4174
const baseUrl = `http://127.0.0.1:${port}`
const viteCli = resolve(frontendDirectory, 'node_modules/vite/bin/vite.js')

async function bundleText() {
  const assetsDirectory = resolve(distDirectory, 'assets')
  const assetNames = (await readdir(assetsDirectory)).filter(name => name.endsWith('.js'))
  assert(assetNames.length > 0, `no JavaScript assets found under ${assetsDirectory}`)
  const contents = await Promise.all(assetNames.map(name => readFile(resolve(assetsDirectory, name), 'utf8')))
  return contents.join('\n')
}

async function waitForPreview() {
  const deadline = Date.now() + 15_000
  let lastError = null
  while (Date.now() < deadline) {
    try {
      const response = await fetch(baseUrl)
      if (response.ok) return
    } catch (error) {
      lastError = error
    }
    await new Promise(resolveDelay => setTimeout(resolveDelay, 100))
  }
  throw new Error(`Vite preview did not become ready${lastError ? `: ${lastError.message}` : ''}`)
}

const bundle = await bundleText()
for (const marker of ['demo-public-2026', '公开演示账号：demo']) {
  assert(bundle.includes(marker), `production bundle is missing public-demo marker: ${marker}`)
}

const forbiddenMarkers = [
  'zhangsan',
  'DEMO_INTERVIEW_PASSWORD',
  'DEMO_ADMIN_PASSWORD',
  'DEMO_AUTH_DEFAULT_PASSWORD',
  'ADMIN_TOKEN',
  'MOCK_OA_WEBHOOK_SECRET',
  'AUTH_JWT_SECRET',
  'JAVA_INTERNAL_TOKEN',
  'DEEPSEEK_API_KEY',
]
for (const marker of forbiddenMarkers) {
  assert(!bundle.includes(marker), `production bundle contains forbidden server-side marker: ${marker}`)
}

const preview = spawn(process.execPath, [viteCli, 'preview', '--host', '127.0.0.1', '--port', String(port)], {
  cwd: frontendDirectory,
  env: { ...process.env, NODE_ENV: 'production' },
  stdio: 'ignore',
})

let browser
try {
  await waitForPreview()
  const { chromium } = await import('@playwright/test')
  browser = await chromium.launch()
  const page = await browser.newPage()
  let loginPayload = null
  await page.route('**/api/auth/login', async route => {
    loginPayload = route.request().postDataJSON()
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        accessToken: 'production-build-verification-token',
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

  await page.goto(baseUrl)
  assert.equal(await page.getByLabel('用户名').inputValue(), 'demo')
  assert.equal(await page.getByLabel('密码').inputValue(), 'demo-public-2026')
  await page.getByText('公开演示账号：demo（仅支持只读能力）').waitFor()

  const loginButton = page.getByRole('button', { name: '登录' })
  assert.equal(await loginButton.isDisabled(), false)
  await loginButton.click()
  await page.getByRole('heading', { name: '智能体问答' }).waitFor()
  assert.deepEqual(loginPayload, { username: 'demo', password: 'demo-public-2026' })
} finally {
  await browser?.close()
  preview.kill()
}

console.log('production frontend build verification passed')
