import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Served at https://<user>.github.io/EdgeLab/
export default defineConfig({
  base: '/EdgeLab/',
  plugins: [react(), tailwindcss()],
})
