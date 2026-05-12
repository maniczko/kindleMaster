import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

export default defineConfig({
  root: "frontend",
  base: "/static/react/",
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./frontend/src", import.meta.url)),
    },
  },
  build: {
    outDir: "../static/react",
    emptyOutDir: true,
    manifest: true,
  },
  server: {
    proxy: {
      "/convert": "http://127.0.0.1:5001",
      "/analyze": "http://127.0.0.1:5001",
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./frontend/src/test/setup.ts"],
  },
});
