import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发代理：/api → 本地 FastAPI(8000)，剥掉 /api 前缀（后端路由无前缀）。
// 构建产物由 FastAPI 静态托管。
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    // /mnt/d (WSL 挂载盘) 上 inotify 不可靠 → HMR 收不到文件变化、module graph 缓存旧代码。
    // 改轮询监听后，改码即时热更新，无需每次重启 vite。
    watch: { usePolling: true, interval: 500 },
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
