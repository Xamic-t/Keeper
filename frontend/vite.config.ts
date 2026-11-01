import { fileURLToPath, URL } from 'node:url'

import tailwindcss from '@tailwindcss/vite'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [vue(), tailwindcss()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    // 验收时要和日常开发并存：e2e 起一份指向新后端的实例，不抢 5173
    port: Number(process.env.VITE_PORT ?? 5173),
    host: true,
    // 端口被占时直接报错，别自己往上跳。
    // 验收脚本按固定端口写地址，偷偷跳到 5174/5175 会让 e2e 连到旧进程上，查半天。
    strictPort: true,
    proxy: {
      // 开发时把 API 代理到后端，前端代码里永远用同源 /api
      '/api': {
        target: process.env.VITE_API_TARGET ?? 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
})
