import { test, expect } from "@playwright/test";

test("transcription cooldown explains the wait and returns to normal progress", async ({
  page,
  context,
}) => {
  await context.addCookies([
    {
      name: "sumx_session",
      value: "browser-test-token",
      domain: "localhost",
      path: "/",
    },
  ]);
  let retryAfter = 120;
  await page.route(
    /\/api\/meetings\/cooldown-fixture(?:\/summaries|\/status)?$/,
    async (route) => {
      const modes = Object.fromEntries(
        ["concise", "standard", "detailed"].map((mode) => [
          mode,
          {
            mode,
            exists: false,
            status: "not_generated",
            error: null,
          },
        ]),
      );
      const body = route.request().url().endsWith("/summaries")
        ? { modes, latest_mode: null, narration_options: [], legacy: null }
        : {
            id: "cooldown-fixture",
            title: "Cooldown fixture",
            created: Date.now() / 1000,
            duration: 2700,
            status: "transcribing",
            error: null,
            detail: "standard",
            transcript_version: 0,
            summary_version: 0,
            retain_source: true,
            job: {
              id: "cooldown-job",
              kind: "pipeline",
              state: "running",
              stage: "transcribing",
              completed: 2,
              total: 15,
              error: null,
              retry_after_seconds: retryAfter,
            },
          };
      await route.fulfill({ json: body });
    },
  );
  await page.goto("/meetings/cooldown-fixture");
  await expect(
    page.getByText("Waiting for transcription service", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("2 of 15 parts complete", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText(/Retrying in about 120 seconds/)).toBeVisible();
  retryAfter = 0;
  await expect(
    page.getByText("Transcribing audio", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText(/Retrying in about/)).toHaveCount(0);
});
