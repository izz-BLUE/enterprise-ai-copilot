import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { build } from 'vite'
import {
  applyProductionBuildEnvironment,
  productionViteConfig,
} from './production-build-contract.mjs'

const scriptDirectory = dirname(fileURLToPath(import.meta.url))
const frontendDirectory = resolve(scriptDirectory, '..')

// Only deliberately public demo values cross the Vite build boundary. Vite's
// envDir=false also prevents any .env* file from contributing VITE_* values.
applyProductionBuildEnvironment()

try {
  await build(productionViteConfig(frontendDirectory))
} catch (error) {
  const message = error instanceof Error ? error.message : String(error)
  console.error(`production frontend build failed: ${message}`)
  process.exitCode = 1
}
