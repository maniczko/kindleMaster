import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

export default defineConfig(({ mode }) => {
  const isVercelBuild =
    mode === "vercel" ||
    process.env.KINDLEMASTER_DEPLOY_TARGET === "vercel" ||
    process.env.VERCEL === "1";

  return {
    root: "frontend",
    base: isVercelBuild ? "/" : "/static/react/",
    plugins: [react()],
    resolve: {
      alias: {
        "@": fileURLToPath(new URL("./frontend/src", import.meta.url)),
      },
    },
    build: {
      outDir: isVercelBuild ? "../dist/vercel" : "../static/react",
      emptyOutDir: true,
      manifest: true,
    },
    server: {
      proxy: {
        "/auth": "http://127.0.0.1:5001",
        "/convert": "http://127.0.0.1:5001",
        "/analyze": "http://127.0.0.1:5001",
        "/user": "http://127.0.0.1:5001",
      },
    },
    test: {
      environment: "jsdom",
      globals: true,
      setupFiles: ["./frontend/src/test/setup.ts"],
    },
  };
});
