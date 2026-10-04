import { defineConfig } from "vitest/config";
import path from "node:path";

// Minimal config for the pure-function test suite (ws-protocol, bboxToH3Cells).
// No DOM/jsdom environment - these are plain Node-runnable unit tests, and
// adding a browser-like environment would be scope the project doesn't need
// yet (component tests are explicitly out of scope for this pass).
export default defineConfig({
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
