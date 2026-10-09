import { existsSync } from "node:fs";
import { defineConfig } from "playwright/test";
export default defineConfig({
  testDir: ".",
  testMatch: "*.spec.mjs",
  workers: 1,
  timeout: 120000,
  expect: { timeout: 10000 },
  reporter: [
    ["list"],
    ["json", { outputFile: "../../.local/browser-results.json" }],
  ],
  use: {
    baseURL: process.env.WKS_BROWSER_URL || "http://127.0.0.1:8082",
    headless: true,
    viewport: { width: 1512, height: 982 },
    trace: "off",
    video: "off",
    launchOptions: {
      executablePath:
        process.env.WKS_TEST_CHROMIUM_PATH ||
        (existsSync("/usr/bin/chromium") ? "/usr/bin/chromium" : undefined),
      args: ["--no-sandbox", "--disable-dev-shm-usage"],
    },
  },
});
