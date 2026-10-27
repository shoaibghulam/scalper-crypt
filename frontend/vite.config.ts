import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

/**
 * The production build is static files served by FastAPI from the same origin,
 * so there is no Node runtime to supervise on a host whose entire job is
 * keeping a trading engine alive, and no CORS configuration to get wrong.
 *
 * In development the dev server proxies to the API so the frontend still sees
 * a single origin and the code path does not change between environments.
 */
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    sourcemap: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:5177",
        changeOrigin: false,
      },
    },
  },
});
