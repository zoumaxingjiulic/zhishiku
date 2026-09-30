import { defineConfig } from "vitest/config";
import vue from "@vitejs/plugin-vue";
export default defineConfig({
  plugins: [vue()],
  build: { outDir: "dist" },
  server: {
    proxy: {
      "/api": { target: process.env.VITE_API_PROXY_TARGET || "http://127.0.0.1:18000", changeOrigin: true },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
  },
});
