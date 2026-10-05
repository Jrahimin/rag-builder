import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    globals: true,
    // Bound jsdom workers so concurrent files do not compete for test wait budgets.
    fileParallelism: false,
    environment: "jsdom",
    setupFiles: "./src/test/setupTests.ts",
    css: true,
    coverage: { reporter: ["text", "html"] },
  },
});
