import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

/**
 * 构建产物直接落到 ../web/dist，由后端静态挂载。
 *
 * 开发时 `pnpm dev` 起在 5173，通过 proxy 访问真实后端 127.0.0.1:8801，
 * 这样不用把整个 studio 跑两遍。
 */
export default defineConfig({
  plugins: [react()],
  // SPA 入口是 /app，静态资源通过后端 /static/dist/ 挂载。
  // Router 用 basename="/app" 匹配。
  base: '/static/dist/',
  build: {
    outDir: '../web/dist',
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8801',
      '/assets': 'http://127.0.0.1:8801',
      '/media': 'http://127.0.0.1:8801',
      '/outputs': 'http://127.0.0.1:8801',
      '/static': 'http://127.0.0.1:8801',
      '/app': { target: 'http://127.0.0.1:8801', changeOrigin: true },
    },
  },
})
