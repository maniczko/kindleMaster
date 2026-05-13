import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    globals: true,
    include: ["**/*.test.js", "**/*.test.ts", "**/*.test.tsx"],
    setupFiles: ["./frontend/src/test/setup.ts"],
  },
});
