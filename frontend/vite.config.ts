/// <reference types="vitest" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// ADR-033:开发用 vite 代理(:5173 → :8000),交付由 FastAPI 同源托管
// `frontend/dist`。同源故【无需 CORS 配置】—— CORS 配错是经典的「真白屏」来源。
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      // 归档 PNG 也要走代理:ADR-038 的 png_path 是 `out/...`,
      // 开发态下 :5173 自己没有这些文件。
      '/out': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/setupTests.ts'],
  },
})
