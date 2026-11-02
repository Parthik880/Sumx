import { test, expect } from "@playwright/test";

test("record, consent, preview, save, search, clone narration and delete privately", async ({
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
  await page.goto("/voices");
  await expect(
    page.getByRole("heading", { name: "Your Voices", exact: true }),
  ).toBeVisible();
  await page.getByRole("link", { name: "Create New Voice" }).first().click();
  await page
    .getByLabel("Voice name", { exact: true })
    .fill("Personal browser voice");
  await page
    .getByRole("button", { name: "Start Recording", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Stop Recording", exact: true }),
  ).toBeVisible();
  await page.waitForTimeout(4200);
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Resume", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Resume", exact: true }).click();
  await page
    .getByRole("button", { name: "Stop Recording", exact: true })
    .click();
  await expect(
    page.getByLabel("Your reference recording", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Generate Voice Preview", exact: true }),
  ).toBeDisabled();
  await page
    .getByLabel("I confirm this is my own voice", { exact: false })
    .check();
  await page
    .getByRole("button", { name: "Generate Voice Preview", exact: true })
    .click();
  await expect(page.getByLabel("Generated voice preview")).toBeVisible({
    timeout: 20000,
  });
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: "test-results/voice-studio-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => window.scrollTo(0, 0));
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: "test-results/voice-studio-mobile.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole("button", { name: "Save Voice", exact: true }).click();
  await expect(page).toHaveURL(/\/voices$/);
  await page.getByPlaceholder("Search your voices...").fill("PERSONAL");
  await expect(
    page.getByRole("heading", { name: "Personal browser voice" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Rename", exact: true }).click();
  await page
    .getByRole("dialog")
    .getByLabel("Voice name")
    .fill("Faculty Personal Voice");
  await page.getByRole("button", { name: "Save name", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Faculty Personal Voice" }),
  ).toBeVisible();
  await page
    .getByRole("link", { name: "Meeting history", exact: true })
    .click();
  await page
    .getByRole("link", { name: "Voice studio meeting", exact: true })
    .click();
  await page.getByRole("tab", { name: "Recording & narration" }).click();
  await page
    .getByRole("button", { name: "Cloned Voices", exact: true })
    .click();
  await page.getByLabel("Search cloned voices").fill("Personal");
  await page
    .getByLabel("Select a cloned voice")
    .selectOption({ label: "Faculty Personal Voice" });
  await page
    .getByRole("button", { name: "Generate narration", exact: true })
    .click();
  await expect(
    page.getByRole("region", { name: "Narration generation progress" }),
  ).toBeVisible();
  const narration = page
    .locator(".narration")
    .filter({ has: page.getByText("Faculty Personal Voice", { exact: true }) });
  await expect(
    narration.getByRole("link", { name: "Download MP3" }),
  ).toBeVisible({
    timeout: 20000,
  });
  await expect(narration.locator("strong")).toHaveText(
    "Faculty Personal Voice",
  );
  await expect(
    page.locator('.narration-progress [role="progressbar"]'),
  ).toHaveAttribute("aria-valuenow", "100");
  await page.reload();
  await page.getByRole("tab", { name: "Recording & narration" }).click();
  await expect(
    page.locator('.narration-progress [role="progressbar"]'),
  ).toHaveAttribute("aria-valuenow", "100");
  const audio = narration.locator("audio");
  await audio.evaluate(async (el: HTMLAudioElement) => {
    el.load();
    await new Promise<void>((resolve, reject) => {
      el.onloadedmetadata = () => resolve();
      el.onerror = () => reject(new Error("Invalid audio"));
    });
  });
  expect(
    await audio.evaluate((el: HTMLAudioElement) => el.duration),
  ).toBeGreaterThan(0);
  await page.getByRole("link", { name: "Voice Cloning", exact: true }).click();
  await page
    .getByRole("button", { name: "Delete Faculty Personal Voice", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Delete voice permanently", exact: true })
    .click();
  await expect(
    page.getByText("No voices yet. Create your first voice", { exact: false }),
  ).toBeVisible();
});

test("unavailable cloning capability keeps presets available", async ({
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
  await page.route("**/api/voices/capabilities", (route) =>
    route.fulfill({
      json: {
        model: "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
        supported: false,
        state: "offline",
        gpu_available: false,
        message: "The GPU voice service is unavailable.",
      },
    }),
  );
  await page.goto("/voices/new");
  await expect(
    page.getByText("The GPU voice service is unavailable."),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Generate Voice Preview", exact: true }),
  ).toBeDisabled();
  await page
    .getByRole("link", { name: "Meeting history", exact: true })
    .click();
  await page
    .getByRole("link", { name: "Voice studio meeting", exact: true })
    .click();
  await page.getByRole("tab", { name: "Recording & narration" }).click();
  await expect(
    page.getByRole("button", { name: "Load available voices" }),
  ).toBeEnabled();
});

test("stored preset previews play with no inference call and switching stops playback", async ({
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
  const generation: string[] = [];
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      /\/preview|\/speech|\/narrate/.test(request.url())
    )
      generation.push(request.url());
  });
  await page.goto("/history");
  await page
    .getByRole("link", { name: "Voice studio meeting", exact: true })
    .click();
  await page.getByRole("tab", { name: "Recording & narration" }).click();
  await page.getByRole("button", { name: "Load available voices" }).click();
  const select = page.getByLabel("Narration voice", { exact: true });
  await expect(select).toHaveValue("en_paul_neutral");
  expect(
    await select
      .locator("option")
      .evaluateAll((options) =>
        options.map((option) => (option as HTMLOptionElement).value),
      ),
  ).toEqual([
    "en_paul_neutral",
    "en_paul_confident",
    "gb_oliver_neutral",
    "gb_oliver_confident",
    "gb_jane_neutral",
    "gb_jane_confident",
  ]);
  await select.selectOption("gb_oliver_neutral");
  const audio = page.getByLabel("Preset voice preview");
  await expect(audio).toHaveAttribute("src", /gb_oliver_neutral/);
  await expect
    .poll(() => audio.evaluate((element: HTMLAudioElement) => element.duration))
    .toBeGreaterThan(0);
  const preview = page.getByRole("button", {
    name: "Preview voice",
    exact: true,
  });
  await preview.click();
  await preview.click();
  await select.selectOption("gb_jane_neutral");
  await expect(audio).toHaveAttribute("src", /gb_jane_neutral/);
  expect(await audio.evaluate((e: HTMLAudioElement) => e.paused)).toBe(true);
  // The last preset deliberately has no stored file.
  await select.selectOption("gb_jane_confident");
  await expect(
    page.getByRole("button", { name: "Preview unavailable" }),
  ).toBeDisabled();
  await expect(audio).toHaveCount(0);
  expect(generation).toEqual([]);
  await page.reload();
  await page.getByRole("tab", { name: "Recording & narration" }).click();
  await page.getByRole("button", { name: "Load available voices" }).click();
  await expect(select).toHaveValue("gb_jane_confident");
  // Leave the shared browser fixture at its default for other workflow tests.
  await select.selectOption("en_paul_neutral");
});
