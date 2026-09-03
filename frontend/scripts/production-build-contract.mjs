export const PUBLIC_DEMO_ENVIRONMENT = Object.freeze({
  VITE_DEMO_AUTH_ENABLED: 'true',
  VITE_PUBLIC_DEMO_USERNAME: 'demo',
  VITE_PUBLIC_DEMO_PASSWORD: 'demo-public-2026',
})

export function applyProductionBuildEnvironment(environment = process.env) {
  for (const name of Object.keys(environment)) {
    if (name.startsWith('VITE_')) delete environment[name]
  }
  Object.assign(environment, PUBLIC_DEMO_ENVIRONMENT)
}

export function productionViteConfig(root) {
  return {
    root,
    mode: 'production',
    envDir: false,
  }
}
