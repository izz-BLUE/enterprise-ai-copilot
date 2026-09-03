import assert from 'node:assert/strict'
import { access, mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { build } from 'vite'
import {
  applyProductionBuildEnvironment,
  productionViteConfig,
} from './production-build-contract.mjs'

const originalViteEnvironment = Object.fromEntries(
  Object.entries(process.env).filter(([name]) => name.startsWith('VITE_')),
)
const fixtureDirectory = await mkdtemp(join(tmpdir(), 'enterprise-ai-copilot-production-env-'))

try {
  await writeFile(resolve(fixtureDirectory, 'index.html'), '<script type="module" src="/main.js"></script>')
  await writeFile(resolve(fixtureDirectory, 'main.js'), `
    document.body.textContent = JSON.stringify({
      enabled: import.meta.env.VITE_DEMO_AUTH_ENABLED,
      username: import.meta.env.VITE_PUBLIC_DEMO_USERNAME,
      password: import.meta.env.VITE_PUBLIC_DEMO_PASSWORD,
      processCanary: import.meta.env.VITE_FORBIDDEN_CANARY,
      envCanary: import.meta.env.VITE_FORBIDDEN_ENV_CANARY,
    })
  `)
  await writeFile(resolve(fixtureDirectory, '.env'), 'VITE_FORBIDDEN_ENV_CANARY=env-should-not-leak\n')
  await writeFile(resolve(fixtureDirectory, '.env.local'), 'VITE_FORBIDDEN_ENV_CANARY=local-should-not-leak\n')
  await writeFile(resolve(fixtureDirectory, '.env.production'), 'VITE_FORBIDDEN_ENV_CANARY=production-should-not-leak\n')
  await writeFile(resolve(fixtureDirectory, '.env.production.local'), 'VITE_FORBIDDEN_ENV_CANARY=env-file-should-not-leak\n')

  process.env.VITE_FORBIDDEN_CANARY = 'should-not-leak'
  applyProductionBuildEnvironment()

  await build({
    ...productionViteConfig(fixtureDirectory),
    configFile: false,
  })

  const assetsDirectory = resolve(fixtureDirectory, 'dist/assets')
  const javascriptAssets = (await readdir(assetsDirectory)).filter(name => name.endsWith('.js'))
  assert(javascriptAssets.length > 0, 'no JavaScript fixture assets found')
  const bundle = (await Promise.all(javascriptAssets.map(name => readFile(join(assetsDirectory, name), 'utf8')))).join('\n')

  for (const marker of ['true', 'demo', 'demo-public-2026']) {
    assert(bundle.includes(marker), `public-demo marker missing from fixture bundle: ${marker}`)
  }
  for (const marker of [
    'should-not-leak',
    'env-should-not-leak',
    'local-should-not-leak',
    'production-should-not-leak',
    'env-file-should-not-leak',
  ]) {
    assert(!bundle.includes(marker), `forbidden VITE canary leaked into fixture bundle: ${marker}`)
  }
} finally {
  for (const name of Object.keys(process.env).filter(name => name.startsWith('VITE_'))) {
    delete process.env[name]
  }
  Object.assign(process.env, originalViteEnvironment)
  await rm(fixtureDirectory, { recursive: true, force: true })
}

await assert.rejects(access(fixtureDirectory), /ENOENT/)
console.log('production env-file boundary verification passed')
