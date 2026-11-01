import { test, expect } from "@playwright/test";
import { mockGoogleScript } from "./googleMock";

test("public navigation, login and mobile appearance", async ({ page }) => {
  await mockGoogleScript(page);
  await page.goto("/");
  await expect(
    page.getByRole("heading", {
      name: "Good conversations. Clearer next steps.",
    }),
  ).toBeVisible();
  await page.getByRole("link", { name: "Sign in", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Continue with Google" }),
  ).toBeVisible();
  await page.goto("/admin/login");
  await expect(page.getByLabel("Password", { exact: true })).toHaveAttribute(
    "type",
    "password",
  );
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
});

test("real upload, persisted processing, transcript editing, summary and narration with mocked OpenRouter", async ({
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
  await page.goto("/dashboard");
  await expect(
    page.getByRole("heading", { name: "Your next conversation, remembered." }),
  ).toBeVisible();
  await page.getByRole("link", { name: "Upload meeting", exact: true }).click();
  // One second of PCM silence is a media fixture; provider text is explicitly mocked in e2e_server.
  const wav = Buffer.alloc(32044);
  wav.write("RIFF");
  wav.writeUInt32LE(32036, 4);
  wav.write("WAVEfmt ", 8);
  wav.writeUInt32LE(16, 16);
  wav.writeUInt16LE(1, 20);
  wav.writeUInt16LE(1, 22);
  wav.writeUInt32LE(16000, 24);
  wav.writeUInt32LE(32000, 28);
  wav.writeUInt16LE(2, 32);
  wav.writeUInt16LE(16, 34);
  wav.write("data", 36);
  wav.writeUInt32LE(32000, 40);
  await page
    .getByLabel("Choose a meeting file")
    .setInputFiles({ name: "meeting.wav", mimeType: "audio/wav", buffer: wav });
  await page.getByLabel("Meeting title").fill("Browser workflow meeting");
  await page.getByLabel("I have participant consent").check();
  await page.getByRole("button", { name: "Create meeting minutes" }).click();
  await expect(page).toHaveURL(/\/meetings\//);
  await expect(
    page.getByText("The group discussed library opening hours.", {
      exact: true,
    }),
  ).toBeVisible({ timeout: 20000 });
  await page.getByRole("tab", { name: "Transcript", exact: true }).click();
  await expect(
    page.getByText("We discussed library opening hours.", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Edit transcript" }).click();
  await page
    .getByLabel("Transcript text")
    .fill("The group reviewed the library schedule. No owner was assigned.");
  await page.getByRole("button", { name: "Save transcript" }).click();
  await page.getByRole("tab", { name: "Summary", exact: true }).click();
  await expect(
    page.getByText("The transcript has changed.", { exact: false }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Regenerate", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Edit", exact: true }),
  ).toBeEnabled({ timeout: 20000 });
  await page.getByRole("tab", { name: "Action items", exact: true }).click();
  await expect(
    page.getByText("Not specified", { exact: false }).first(),
  ).toBeVisible();
  await page.getByRole("tab", { name: "Recording & narration" }).click();
  await page.getByRole("button", { name: "Load available voices" }).click();
  await expect(page.getByLabel("Narration voice")).toBeVisible();
  await page.getByRole("button", { name: "Generate narration" }).click();
  await expect(page.getByRole("link", { name: "Download MP3" })).toBeVisible({
    timeout: 20000,
  });
  const audio = page.locator(".narration audio");
  await expect(audio).toHaveAttribute("src", /\/audio\//);
  await page.getByRole("link", { name: "Settings", exact: true }).click();
  await page.emulateMedia({ colorScheme: "dark" });
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await expect(page.locator("html")).toHaveCSS("color-scheme", "light");
  await page.screenshot({
    path: "test-results/settings-light.png",
    fullPage: true,
  });
  await page
    .getByRole("link", { name: "Meeting history", exact: true })
    .click();
  await page
    .getByRole("link", { name: "Browser workflow meeting", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Delete meeting", exact: true })
    .click();
  await page.getByRole("button", { name: "Delete permanently" }).click();
  await expect(page).toHaveURL(/\/history/);
  await expect(
    page.getByRole("heading", {
      name: "A fresh start for your meeting history.",
    }),
  ).toBeVisible();
});

test("microphone denied permission is actionable", async ({
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
  await page.addInitScript(() => {
    navigator.mediaDevices.getUserMedia = async () => {
      throw new DOMException("Denied", "NotAllowedError");
    };
  });
  await page.goto("/record");
  await page.getByRole("button", { name: "Start recording" }).click();
  await expect(
    page.getByText("Microphone access was denied.", { exact: false }),
  ).toBeVisible();
});
