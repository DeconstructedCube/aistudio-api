import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'node:path'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 3000,
    proxy: {
      '/auth': 'http://localhost:8080',
      '/stats': 'http://localhost:8080',
      '/accounts': 'http://localhost:8080',
      '/rotation': 'http://localhost:8080',
      '/config': 'http://localhost:8080',
      '/api-keys': 'http://localhost:8080',
      '/health': 'http://localhost:8080',
    },
  },
  build: {
    outDir: path.resolve(__dirname, '../src/aistudio_api/static'),
    emptyOutDir: true,
    rollupOptions: {
      output: {
        entryFileNames: 'assets/[name].js',
        chunkFileNames: 'assets/[name].js',
        assetFileNames: 'assets/[name].[ext]',
        manualChunks: {
          'vendor-vue': ['vue', 'vue-router', 'pinia'],
          'vendor-icons': ['lucide-vue-next'],
        },
      },
    },
  },
})
