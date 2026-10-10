import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
const publicOrigin = process.env.PUBLIC_APP_ORIGIN;
const allowedHosts = publicOrigin ? [new URL(publicOrigin).hostname] : [];
const proxy = { '/api': process.env.API_TARGET || 'http://127.0.0.1:8000' };
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true, allowedHosts, proxy },
  preview: { port: 5173, strictPort: true, allowedHosts, proxy },
});
