import { test, expect } from "@playwright/test";

test("MediaRecorder pause, resume, stop and upload use the same durable pipeline", async ({
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
  await page.goto("/record");
  await page.getByRole("button", { name: "Start recording" }).click();
  await expect(
    page.getByText("Recording your microphone", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  await expect(
    page.getByText("Recording paused", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Resume", exact: true }).click();
  await expect(page.locator(".record-time")).not.toHaveText("0:00");
  await page.getByRole("button", { name: "Stop recording" }).click();
  await expect(
    page.getByRole("link", { name: "Save a local copy" }),
  ).toBeVisible();
  await page.getByLabel("Meeting title").fill("Microphone browser test");
  await page.getByLabel("I have participant consent").check();
  await page.getByRole("button", { name: "Create meeting minutes" }).click();
  await expect(page).toHaveURL(/\/meetings\//);
  await expect(
    page.getByText("The group discussed library opening hours.", {
      exact: true,
    }),
  ).toBeVisible({ timeout: 20000 });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: "test-results/meeting-mobile.png",
    fullPage: true,
  });
  await page
    .getByRole("button", { name: "Delete meeting", exact: true })
    .click();
  await page.getByRole("button", { name: "Delete permanently" }).click();
  await expect(page).toHaveURL(/\/history/);
});
