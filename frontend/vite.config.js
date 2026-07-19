import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import process from 'node:process'

const javaProxyTarget = process.env.JAVA_PROXY_TARGET || 'http://localhost:8080'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: javaProxyTarget,
        changeOrigin: true,
      },
    },
  },
})
