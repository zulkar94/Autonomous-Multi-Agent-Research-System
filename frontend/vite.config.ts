import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dev server proxies the API so the browser sees a single origin, which
// keeps CORS and the CSP identical in development and production.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
      "/healthz": "http://localhost:8000",
    },
  },
  build: { outDir: "dist", sourcemap: false, target: "es2020" },
});
