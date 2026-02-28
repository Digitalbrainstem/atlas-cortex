import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  base: '/admin/',
  server: {
    proxy: {
      '/admin/auth': 'http://localhost:5100',
      '/admin/dashboard': 'http://localhost:5100',
      '/admin/users': 'http://localhost:5100',
      '/admin/safety': 'http://localhost:5100',
      '/admin/voice': 'http://localhost:5100',
      '/admin/devices': 'http://localhost:5100',
      '/admin/evolution': 'http://localhost:5100',
      '/admin/system': 'http://localhost:5100',
    },
  },
})
