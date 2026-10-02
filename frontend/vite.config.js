import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// React dev server port 3000, /api requests ko port 5000 wale Python API pe proxy karo
export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://localhost:5000',
        changeOrigin: true,
      },
    },
  },
})
