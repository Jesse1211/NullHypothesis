/// <reference types="vitest" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// ADR-033:开发用 vite 代理(:5173 → :8000),交付由 FastAPI 同源托管
// `frontend/dist`。同源故【无需 CORS 配置】—— CORS 配错是经典的「真白屏」来源。
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // 归档图像也走 /api(ADR-045 的专用端点),故**只需**代理 /api ——
      // 与 ADR-033 原文一致。
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/setupTests.ts'],
  },
})
