import { test, expect } from "@playwright/test";

test("dashboard uses real scoped aggregates and follows the light reference layout", async ({
  page,
  context,
}) => {
  await context.addCookies([
    {
      name: "sumx_session",
      value: "analytics-browser-token",
      domain: "localhost",
      path: "/",
    },
  ]);
  await page.setViewportSize({ width: 1672, height: 1000 });
  await page.goto("/dashboard");
  await expect(
    page.getByRole("heading", { name: "Welcome back, Analytics." }),
  ).toBeVisible();
  await expect(page.locator(".overview-stats>div")).toHaveCount(4);
  await expect(page.locator(".overview-actions>a")).toHaveCount(3);
  await expect(page.locator(".recent-table tbody tr")).toHaveCount(6);
  await expect(
    page.getByRole("columnheader", { name: "Total cost", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Spend overview" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Cost breakdown" }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("link", { name: "Analytics", exact: true }),
  ).toHaveCount(0);
  await expect(page.locator(".stats")).toContainText("$1.88");
  await page.screenshot({
    path: "test-results/dashboard-reference-desktop.png",
    fullPage: true,
  });
  await page.getByLabel("Spending date range").selectOption("30");
  await expect(page.locator(".spending-card svg")).toHaveAttribute(
    "aria-label",
    /30 days/,
  );
  await page.getByLabel("Search meetings", { exact: true }).fill("fixture 3");
  await expect(page.locator(".recent-table tbody tr")).toHaveCount(1);
  await page.getByRole("link", { name: "View recent summaries" }).click();
  await expect(
    page.getByLabel("Summaries only", { exact: true }),
  ).toBeChecked();
  await expect(page.locator(".recent-table tbody tr")).toHaveCount(6);
  await page.getByLabel("Sort meetings by date").selectOption("oldest");
  await expect(page.locator(".recent-table tbody tr").first()).toContainText(
    "fixture 8",
  );
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/dashboard");
  await expect(page.locator(".overview-stats>div")).toHaveCount(4);
  await page.screenshot({
    path: "test-results/dashboard-reference-mobile.png",
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.goto("/analytics");
  await expect(page).toHaveURL(/\/dashboard$/);
});

test("administrator analytics filters, expands participant meeting costs and exports CSV", async ({
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
  await page.goto("/analytics");
  await expect(
    page.getByRole("heading", { name: "A clear view of AI spending." }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Cost breakdown", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Model-level costs" }),
  ).toBeVisible();
  await page.getByLabel("Search participants").fill("analytics@college.edu");
  await expect(
    page.locator(".participant-card> .table-scroll>table>tbody>tr"),
  ).toHaveCount(1);
  await expect(
    page.locator(".participant-card tbody>.expanded-row"),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "View meetings for Analytics Test" })
    .click();
  await expect(page.locator(".expanded-row")).toContainText(
    "Analytics fixture 1",
  );
  await page.getByLabel("Spending date range").selectOption("90");
  await expect(page.locator(".spending-card svg")).toHaveAttribute(
    "aria-label",
    /90 days/,
  );
  await expect(page.locator(".cost-analytics-page")).toHaveAttribute(
    "aria-busy",
    "false",
  );
  await expect(
    page.getByRole("link", { name: "Export ledger CSV" }),
  ).toHaveAttribute("href", /days=90/);
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("link", { name: "Export ledger CSV" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("sumx-costs-90-days.csv");
  await page.screenshot({
    path: "test-results/admin-cost-analytics.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
});
