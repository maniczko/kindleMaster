import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  root: "frontend",
  base: "/static/react/",
  plugins: [react()],
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

