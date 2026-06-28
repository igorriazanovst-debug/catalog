import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// SPA раздаётся бэкендом под /app (см. backend/app/main.py), поэтому base=/app/.
// В dev-режиме запросы к /api проксируются на uvicorn (по умолчанию :8001).
export default defineConfig({
  base: "/app/",
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: process.env.VITE_API_TARGET || "http://localhost:8001",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
