import { test, expect } from "@playwright/test";

test("live segment progress, refresh reconnect, no premature 100%, failure and retry", async ({
  page,
  context,
}) => {
  await context.addCookies([
    {
      name: "sumx_session",
      value: "voice-browser-token",
      domain: "localhost",
      path: "/",
    },
  ]);
  let phase = "generating",
    started = false,
    reads = 0,
    retries = 0;
  await page.route("**/api/meetings/*/narrations", async (route) => {
    started = true;
    await route.continue();
  });
  await page.route("**/api/meetings/*/jobs/*/retry", async (route) => {
    retries++;
    phase = "completed";
    await route.fulfill({ json: { state: "queued" } });
  });
  await page.route("**/api/narration/jobs/*/status", async (route) => {
    reads++;
    const actual = await route.fetch();
    const data = await actual.json();
    await route.fulfill({
      json: {
        ...data,
        state:
          phase === "failed"
            ? "failed"
            : phase === "completed"
              ? "completed"
              : "running",
        stage:
          phase === "failed"
            ? "FAILED"
            : phase === "completed"
              ? "COMPLETED"
              : phase === "merging"
                ? "MERGING_AUDIO"
                : "GENERATING_AUDIO",
        completed_segments: phase === "generating" ? 0 : 1,
        total_segments: 1,
        percent: phase === "completed" ? 100 : phase === "merging" ? null : 0,
        segment_audio_seconds: phase === "generating" ? 3.2 : 0,
        stream_events: 5,
        elapsed_seconds: 12,
        error:
          phase === "failed"
            ? "GPU worker interrupted. Retry the narration."
            : null,
      },
    });
  });
  await page.goto("/history");
  await page
    .getByRole("link", { name: "Voice studio meeting", exact: true })
    .click();
  await page.getByRole("tab", { name: "Recording & narration" }).click();
  await page
    .getByRole("button", { name: "Generate narration", exact: true })
    .click();
  const panel = page.getByRole("region", {
    name: "Narration generation progress",
  });
  await expect(panel).toBeVisible();
  await expect(panel.getByRole("progressbar")).toHaveAttribute(
    "aria-valuenow",
    "0",
  );
  await expect(
    panel.getByText("Receiving audio", { exact: false }),
  ).toBeVisible();
  expect(started).toBeTruthy();
  await page.screenshot({
    path: "test-results/narration-live-progress.png",
    fullPage: true,
  });
  await page.reload();
  await page.getByRole("tab", { name: "Recording & narration" }).click();
  await expect(panel.getByRole("progressbar")).toHaveAttribute(
    "aria-valuenow",
    "0",
  );
  phase = "merging";
  await expect(
    panel.getByText("Merging audio segments", { exact: true }),
  ).toBeVisible();
  await expect(panel.getByRole("progressbar")).not.toHaveAttribute(
    "aria-valuenow",
    "100",
  );
  phase = "failed";
  await expect(
    panel.getByText("GPU worker interrupted. Retry the narration."),
  ).toBeVisible();
  await panel
    .getByRole("button", { name: "Retry narration", exact: true })
    .click();
  await expect(panel.getByRole("progressbar")).toHaveAttribute(
    "aria-valuenow",
    "100",
  );
  expect(retries).toBe(1);
  const finishedReads = reads;
  await page.waitForTimeout(1600);
  expect(reads).toBe(finishedReads);
});
