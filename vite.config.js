import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Proxy /api and /ws to the FastAPI backend at :8000.
// Override with VITE_API_URL for prod (spec §13 deploys nginx in front of
// both the SPA and the API).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    open: true,
    proxy: {
      // Match with trailing slash so SPA routes like `/api-playground`
      // aren't swallowed by this proxy. All real client calls go through
      // `/api/<path>` (see src/api.js: BASE_URL = '/api'), which still matches.
      '/api/': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ''),
        ws: true,
      },
      '/ws': {
        target: 'ws://localhost:8000',
        ws: true,
      },
    },
  },
});
