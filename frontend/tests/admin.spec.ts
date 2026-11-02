import { test, expect } from "@playwright/test";

test("admin login explains lockout duration and allows retry when it expires", async ({
  page,
}) => {
  await page.route("**/api/admin/login", (route) =>
    route.fulfill({
      status: 429,
      contentType: "application/json",
      headers: { "Retry-After": "2" },
      body: JSON.stringify({ detail: "Too many requests. Try again later." }),
    }),
  );
  await page.goto("/admin");
  await page.getByLabel("Admin ID", { exact: true }).fill("test-admin");
  await page.getByLabel("Password", { exact: true }).fill("wrong-password");
  await page.getByRole("button", { name: "Sign in to administration" }).click();
  await expect(page.getByRole("alert")).toContainText("Try again in 0:02");
  await expect(
    page.getByRole("button", { name: "Please wait to try again" }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "Sign in to administration" }),
  ).toBeEnabled({ timeout: 5000 });
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("admin routes stay separate for anonymous and regular Google sessions", async ({
  page,
  context,
}) => {
  await page.goto("/admin/overview");
  await expect(page).toHaveURL(/\/admin$/);
  await expect(page.getByLabel("Admin ID", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Continue with Google" }),
  ).toHaveCount(0);
  await context.addCookies([
    {
      name: "sumx_session",
      value: "browser-test-token",
      domain: "localhost",
      path: "/",
    },
  ]);
  await page.goto("/admin");
  await expect(page).toHaveURL(/\/admin$/);
  await expect(
    page.getByText(
      "Your regular SUMX session does not grant administrator access.",
    ),
  ).toBeVisible();
  expect((await page.request.get("/api/admin/overview")).status()).toBe(403);
  await page.goto("/dashboard");
  await expect(
    page.getByRole("heading", { name: "Welcome back, Browser." }),
  ).toBeVisible();
});

test("username and password admin login, session and logout", async ({
  page,
}) => {
  await page.goto("/admin");
  await page.getByLabel("Admin ID", { exact: true }).fill("test-admin");
  await page.getByLabel("Password", { exact: true }).fill("wrong-password");
  await page.getByRole("button", { name: "Sign in to administration" }).click();
  await expect(page.getByRole("alert")).toContainText(
    "Admin ID or password is incorrect",
  );
  await page
    .getByLabel("Password", { exact: true })
    .fill("Browser-admin-password-123");
  await page.getByRole("button", { name: "Sign in to administration" }).click();
  await expect(page).toHaveURL(/\/admin\/overview$/);
  await expect(
    page.getByRole("navigation", { name: "Administration" }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Record meeting", exact: true }),
  ).toHaveCount(0);
  await page.goto("/admin");
  await expect(page).toHaveURL(/\/admin\/overview$/);
  await page.getByRole("button", { name: "Logout", exact: true }).click();
  await expect(page).toHaveURL(/\/admin$/);
  expect((await page.request.get("/api/admin/session")).status()).toBe(401);
});

test("admin aggregates, user details, CSV exports, access management and mobile layout", async ({
  page,
  context,
}) => {
  await context.addCookies([
    {
      name: "sumx_session",
      value: "admin-browser-test-token",
      domain: "localhost",
      path: "/",
    },
  ]);
  await page.setViewportSize({ width: 1672, height: 1000 });
  await page.goto("/admin/overview");
  await expect(page.locator(".admin-stats > div")).toHaveCount(6);
  await page
    .getByLabel("Search users", { exact: true })
    .fill("analytics@college.edu");
  await expect(page.locator(".admin-directory tbody tr")).toHaveCount(1);
  await expect(page.locator(".admin-directory tbody")).toContainText("$1.88");
  await expect(
    page.getByText("Analytics fixture 1", { exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: /Total meetings/ }).click();
  await expect(page).toHaveURL(/sort=meetings/);
  const allDownload = page.waitForEvent("download");
  await page.getByRole("link", { name: "Export All Users CSV" }).click();
  expect((await allDownload).suggestedFilename()).toBe("sumx-users-costs.csv");
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: "test-results/admin-overview-desktop.png",
    fullPage: true,
  });
  await page.getByRole("link", { name: "View Details", exact: true }).click();
  await expect(page).toHaveURL(/\/admin\/users\/[a-f0-9]+/);
  await expect(
    page.getByRole("heading", { name: "Analytics Test", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".admin-meetings tbody > tr")).toHaveCount(8);
  await expect(page.getByRole("img", { name: /Recorded LLM/ })).toBeVisible();
  await page.getByRole("button", { name: "Inspect usage" }).first().click();
  await expect(
    page.getByRole("region", { name: "Meeting usage records" }),
  ).toContainText("openrouter");
  const userDownload = page.waitForEvent("download");
  await page.getByRole("link", { name: "Export User CSV" }).click();
  expect((await userDownload).suggestedFilename()).toMatch(
    /^sumx-user-meetings-[a-f0-9]+\.csv$/,
  );
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: "test-results/admin-user-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: "test-results/admin-user-mobile.png",
    fullPage: true,
  });
  await page.getByRole("link", { name: "Add / manage users" }).click();
  await page
    .getByLabel("Email address", { exact: true })
    .fill("New.Approved@example.edu");
  await page.getByRole("button", { name: "Add User", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("Email approved");
  await page
    .getByLabel("Search approved emails")
    .fill("new.approved@example.edu");
  await expect(page.locator("tbody tr")).toHaveCount(1);
  await expect(page.locator("tbody")).toContainText(
    "Approved — Not Yet Registered",
  );
  await page
    .getByLabel("Email address", { exact: true })
    .fill("new.approved@example.edu");
  await page.getByRole("button", { name: "Add User", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("already approved");
  await page
    .getByRole("button", { name: "Revoke Access", exact: true })
    .click();
  await expect(page.locator("tbody")).toContainText("Revoked");
  await page
    .getByRole("button", { name: "Restore Access", exact: true })
    .click();
  await expect(page.locator("tbody")).toContainText(
    "Approved — Not Yet Registered",
  );
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
});
