import { defineConfig, devices } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  fullyParallel: false,
  workers: 1,
  use: { baseURL: "http://localhost:5174", trace: "retain-on-failure" },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        channel: "chrome",
        launchOptions: {
          args: [
            "--use-fake-device-for-media-stream",
            "--use-fake-ui-for-media-stream",
          ],
        },
      },
    },
  ],
  webServer: [
    {
      command:
        process.platform === "win32"
          ? ".venv\\Scripts\\python -m backend.tests.e2e_server"
          : ".venv/bin/python -m backend.tests.e2e_server",
      cwd: "..",
      url: "http://127.0.0.1:8100/api/health",
      reuseExistingServer: false,
    },
    {
      command: "npm run dev -- --port 5174 --strictPort",
      url: "http://localhost:5174",
      env: { API_TARGET: "http://127.0.0.1:8100" },
      reuseExistingServer: false,
    },
  ],
});
