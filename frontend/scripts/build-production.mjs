import { spawnSync } from 'node:child_process'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const scriptDirectory = dirname(fileURLToPath(import.meta.url))
const frontendDirectory = resolve(scriptDirectory, '..')
const viteCli = resolve(frontendDirectory, 'node_modules/vite/bin/vite.js')

// Only these deliberately public values may cross the Vite build boundary.
// Remove inherited VITE_* values first so a developer or CI environment cannot
// silently change the public-demo contract or inject an unrelated value.
const buildEnvironment = Object.fromEntries(
  Object.entries(process.env).filter(([name]) => !name.startsWith('VITE_')),
)
Object.assign(buildEnvironment, {
  VITE_DEMO_AUTH_ENABLED: 'true',
  VITE_PUBLIC_DEMO_USERNAME: 'demo',
  VITE_PUBLIC_DEMO_PASSWORD: 'demo-public-2026',
})

const result = spawnSync(process.execPath, [viteCli, 'build', '--mode', 'production'], {
  cwd: frontendDirectory,
  env: buildEnvironment,
  stdio: 'inherit',
})

if (result.error) {
  console.error(`production frontend build failed: ${result.error.message}`)
  process.exit(1)
}

process.exit(result.status ?? 1)
