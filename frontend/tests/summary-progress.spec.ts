import { test, expect } from "@playwright/test";

test("notes finishing never means completed; slow polling stays single-flight and content is fetched once", async ({
  page,
  context,
}, testInfo) => {
  test.setTimeout(45000);
  await context.addCookies([
    {
      name: "sumx_session",
      value: "browser-test-token",
      domain: "localhost",
      path: "/",
    },
  ]);
  let stage = "summary_notes",
    done = 4,
    saved = false;
  let fullReads = 0,
    overviewReads = 0,
    recordReads = 0,
    statusReads = 0,
    concurrent = 0,
    peak = 0;
  const timings: unknown[] = [];
  page.on("console", async (message) => {
    if (message.text().startsWith("sumx_summary_timing"))
      timings.push(await message.args()[1]?.jsonValue());
  });
  const started = Date.now() / 1000 - 15;
  const meeting = () => ({
    id: "progress-fixture",
    title: "Pipeline progress fixture",
    created: started,
    duration: 2700,
    status: saved ? "ready" : "summarizing",
    error: null,
    detail: "standard",
    transcript_version: 1,
    summary_version: saved ? 1 : 0,
    retain_source: true,
    job: {
      id: "summary-progress-job",
      kind: "summarize",
      state: saved ? "completed" : "running",
      stage,
      completed: done,
      total: 7,
      error: null,
      started_at: started,
      completed_at: saved ? Date.now() / 1000 : undefined,
      summary_progress: { notes_completed: 4, notes_total: 4 },
    },
  });
  await page.route(
    /\/api\/meetings\/progress-fixture(?:\/status|\/summaries(?:\/standard)?)?$/,
    async (route) => {
      const url = route.request().url();
      if (url.endsWith("/status")) {
        statusReads++;
        concurrent++;
        peak = Math.max(peak, concurrent);
        const result = meeting();
        await new Promise((resolve) => setTimeout(resolve, 1800));
        concurrent--;
        await route.fulfill({ json: result });
      } else if (url.endsWith("/summaries/standard")) {
        recordReads++;
        await new Promise((resolve) => setTimeout(resolve, 450));
        await route.fulfill({
          json: {
            mode: "standard",
            exists: true,
            version: 1,
            transcript_version: 1,
            audio: [],
            content: {
              title: "Saved minutes",
              executive_summary: "Validated minutes are now saved.",
              discussion_points: ["Full meeting discussed"],
              decisions: [],
              action_items: [],
              unresolved_issues: [],
              follow_up: [],
            },
          },
        });
      } else if (url.endsWith("/summaries")) {
        overviewReads++;
        await route.fulfill({
          json: {
            latest_mode: "standard",
            narration_options: saved ? ["standard"] : [],
            legacy: null,
            modes: Object.fromEntries(
              ["concise", "standard", "detailed"].map((mode) => [
                mode,
                {
                  mode,
                  exists: saved && mode === "standard",
                  status:
                    saved && mode === "standard" ? "generated" : "generating",
                  error: null,
                  version: saved && mode === "standard" ? 1 : undefined,
                  audio_count: 0,
                },
              ]),
            ),
          },
        });
      } else {
        fullReads++;
        await route.fulfill({
          json: {
            ...meeting(),
            transcript: {
              version: 1,
              text: "A stored transcript.",
              segments: [],
            },
            audio: [],
          },
        });
      }
    },
  );
  await page.goto("/meetings/progress-fixture");
  await expect(
    page.getByText("Generating meeting notes", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("4 of 4 meeting notes ready")).toBeVisible();
  // Development StrictMode may abort an initial mount request and start it again.
  const initialFullReads = fullReads,
    initialOverviewReads = overviewReads;
  const progress = page.getByRole("progressbar", {
    name: "Completed processing parts",
  });
  expect(Number(await progress.getAttribute("value"))).toBeLessThan(100);
  const elapsed = await page.getByText(/^Elapsed /).innerText();
  await expect(page.getByText(/^Elapsed /)).not.toHaveText(elapsed);
  for (const [phase, count, label] of [
    ["summary_synthesis", 4, "Combining meeting notes"],
    ["summary_finalizing", 5, "Finalizing summary"],
    ["summary_saving", 6, "Saving meeting"],
  ] as const) {
    stage = phase;
    done = count;
    await expect(page.getByText(label, { exact: true })).toBeVisible({
      timeout: 9000,
    });
    expect(Number(await progress.getAttribute("value"))).toBeLessThan(100);
    // Interaction remains available even while a status request takes longer than the polling interval.
    await page.getByRole("tab", { name: "Transcript", exact: true }).click();
    await expect(
      page.getByRole("tab", { name: "Transcript", exact: true }),
    ).toHaveAttribute("aria-selected", "true");
    await page.getByRole("tab", { name: "Summary", exact: true }).click();
  }
  expect(fullReads).toBe(initialFullReads);
  expect(overviewReads).toBe(initialOverviewReads);
  saved = true;
  stage = "completed";
  done = 7;
  await expect(page.getByText("Your meeting minutes are saved.")).toBeVisible({
    timeout: 9000,
  });
  await expect(progress).toHaveAttribute("value", "100");
  await expect(
    page.getByText("Validated minutes are now saved."),
  ).toBeVisible();
  await page.waitForTimeout(150);
  expect(peak).toBe(1);
  expect(fullReads).toBe(initialFullReads + 1);
  expect(overviewReads).toBe(initialOverviewReads + 1);
  expect(recordReads).toBe(1);
  expect(
    timings.some(
      (item) => (item as { event: string }).event === "summary_committed",
    ),
  ).toBeTruthy();
  await testInfo.attach("summary-browser-timings.json", {
    body: JSON.stringify(
      { fullReads, overviewReads, recordReads, statusReads, peak, timings },
      null,
      2,
    ),
    contentType: "application/json",
  });
});

test("rapid repeated generation clicks submit one summary request", async ({
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
  let requests = 0;
  await page.route(
    /\/api\/meetings\/duplicate-summary(?:\/summaries(?:\/standard\/generate)?)?$/,
    async (route) => {
      const url = route.request().url();
      if (url.endsWith("/generate")) {
        requests++;
        expect(route.request().headers()["idempotency-key"]).toBeTruthy();
        await new Promise((resolve) => setTimeout(resolve, 500));
        await route.fulfill({ json: { id: "deduplicated-job" } });
      } else if (url.endsWith("/summaries")) {
        await route.fulfill({
          json: {
            latest_mode: null,
            narration_options: [],
            legacy: null,
            modes: Object.fromEntries(
              ["concise", "standard", "detailed"].map((mode) => [
                mode,
                {
                  mode,
                  exists: false,
                  status: "not_generated",
                  error: null,
                },
              ]),
            ),
          },
        });
      } else {
        await route.fulfill({
          json: {
            id: "duplicate-summary",
            title: "Duplicate request fixture",
            created: 1793760000,
            duration: 300,
            status: "ready",
            error: null,
            detail: "standard",
            transcript_version: 1,
            summary_version: 0,
            retain_source: true,
            audio: [],
            transcript: {
              version: 1,
              text: "A stored transcript.",
              segments: [],
            },
          },
        });
      }
    },
  );
  await page.goto("/meetings/duplicate-summary");
  const button = page.getByRole("button", {
    name: "Generate summary",
    exact: true,
  });
  await expect(button).toBeEnabled();
  await button.evaluate((element) => {
    (element as HTMLButtonElement).click();
    (element as HTMLButtonElement).click();
  });
  await expect.poll(() => requests).toBe(1);
  await expect(button).toBeEnabled();
  expect(requests).toBe(1);
});
