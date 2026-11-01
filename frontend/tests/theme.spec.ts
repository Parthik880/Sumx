import { test, expect } from "@playwright/test";

test("every screen stays light with OS dark mode and an old dark preference", async ({
  page,
  context,
}) => {
  await page.emulateMedia({ colorScheme: "dark" });
  await page.addInitScript(() => localStorage.setItem("sumx-theme", "dark"));
  for (const path of ["/", "/login", "/admin/login"]) {
    await page.goto(path);
    await expect(page.locator("html")).toHaveCSS(
      "background-color",
      "rgb(245, 245, 247)",
    );
    await expect(page.locator("html")).toHaveCSS("color-scheme", "light");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  }
  await context.addCookies([
    {
      name: "sumx_session",
      value: "browser-test-token",
      domain: "localhost",
      path: "/",
    },
  ]);
  for (const path of [
    "/dashboard",
    "/history",
    "/record",
    "/upload",
    "/settings",
  ]) {
    await page.goto(path);
    await expect(page.locator(".sidebar")).toHaveCSS(
      "background-color",
      "rgb(232, 232, 234)",
    );
    await expect(page.locator("html")).toHaveCSS("color-scheme", "light");
    await expect(page.getByLabel("Loading", { exact: true })).toBeHidden();
    for (const surface of await page
      .locator(".surface,.stats>div,.empty")
      .all())
      await expect(surface).toHaveCSS("background-color", "rgb(255, 255, 255)");
    if (path === "/dashboard") {
      await expect(page.locator(".stats")).toBeVisible();
      await page.screenshot({
        path: "test-results/dashboard-light.png",
        fullPage: true,
      });
    }
  }
  await context.addCookies([
    {
      name: "sumx_session",
      value: "admin-browser-test-token",
      domain: "localhost",
      path: "/",
    },
  ]);
  await page.goto("/admin");
  await expect(
    page.getByRole("heading", { name: "A clear view of your workspace." }),
  ).toBeVisible();
  await expect(page.locator(".surface").first()).toHaveCSS(
    "background-color",
    "rgb(255, 255, 255)",
  );
  await page.screenshot({
    path: "test-results/admin-light.png",
    fullPage: true,
  });
});
