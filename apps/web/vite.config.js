import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

const apiTarget = process.env.VITE_PROXY_TARGET || 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/login.cgi': { target: apiTarget, changeOrigin: true },
      '/logout.cgi': { target: apiTarget, changeOrigin: true },
      '/captcha.cgi': { target: apiTarget, changeOrigin: true },
      '/render.cgi': { target: apiTarget, changeOrigin: true },
      '/health': { target: apiTarget, changeOrigin: true },
      '/favicon.ico': { target: apiTarget, changeOrigin: true },
      '/statics': { target: apiTarget, changeOrigin: true },
      '/site-brand.cgi': { target: apiTarget, changeOrigin: true },
      '/api': { target: apiTarget, changeOrigin: true },
      '/hooks': { target: apiTarget, changeOrigin: true },
      '/pages': { target: apiTarget, changeOrigin: true, ws: true },
    },
  },
})
