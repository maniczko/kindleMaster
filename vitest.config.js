import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    globals: true,
    include: ["**/*.test.js", "**/*.test.ts", "**/*.test.tsx"],
    setupFiles: ["./frontend/src/test/setup.ts"],
    coverage: {
      provider: "v8",
      reporter: ["text", "html", "lcov"],
      reportsDirectory: "reports/coverage/vitest",
      all: true,
      include: ["frontend/src/**/*.{js,ts,tsx}", "static/js/quality-state-adapter.js"],
      exclude: ["**/*.test.*", "**/*.spec.*", "**/*.d.ts", "frontend/src/test/**", "frontend/src/vite-env.d.ts"],
      thresholds: {
        statements: 65,
        branches: 55,
        functions: 65,
        lines: 68,
      },
    },
  },
});
