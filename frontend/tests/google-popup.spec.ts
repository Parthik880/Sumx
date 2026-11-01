import { test, expect } from "@playwright/test";
import { mockGoogleScript } from "./googleMock";

test("official GIS API contract, access denial, popup callback login, switch and logout", async ({
  page,
}) => {
  await mockGoogleScript(page);
  await page.goto("/login");
  await expect(
    page.getByRole("button", { name: "Continue with Google" }),
  ).toBeVisible();
  const config = await page.evaluate(() => {
    const c = (window as any).testGoogleConfig;
    return {
      ux_mode: c.ux_mode,
      auto_select: c.auto_select,
      button_auto_select: c.button_auto_select,
      nonce: !!c.nonce,
    };
  });
  expect(config).toEqual({
    ux_mode: "popup",
    auto_select: false,
    button_auto_select: false,
    nonce: true,
  });
  await page.evaluate(() => {
    (window as any).testGoogleEmail = "unapproved@college.edu";
  });
  await page.getByRole("button", { name: "Continue with Google" }).click();
  await expect(page.getByRole("alert")).toContainText("Access denied");
  await expect(
    page.getByRole("button", { name: "Continue with Google" }),
  ).toBeVisible();
  await page.evaluate(() => {
    (window as any).testGoogleEmail = "popup@college.edu";
  });
  await page.getByRole("button", { name: "Continue with Google" }).click();
  await expect(page).toHaveURL(/\/dashboard$/);
  expect(
    await page.evaluate(() =>
      Object.keys(localStorage).some((k) => /token|credential/i.test(k)),
    ),
  ).toBeFalsy();
  await page.getByRole("link", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "Switch Google account" }).click();
  await expect(page).toHaveURL(/\/login$/);
  expect(
    await page.evaluate(() => (window as any).testAutoSelectDisabled),
  ).toBeTruthy();
  await expect(
    page.getByRole("button", { name: "Continue with Google" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Continue with Google" }).click();
  await expect(page).toHaveURL(/\/dashboard$/);
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await expect(page).toHaveURL(/\/login$/);
  expect((await page.request.get("/api/auth/me")).status()).toBe(401);
});
