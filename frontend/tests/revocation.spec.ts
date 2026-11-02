import { test, expect, type BrowserContext, type Page } from "@playwright/test";
import { mockGoogleScript } from "./googleMock";

async function prepare(page: Page, admin: BrowserContext, email: string) {
  await admin.addCookies([
    {
      name: "sumx_session",
      value: "admin-browser-test-token",
      domain: "localhost",
      path: "/",
    },
  ]);
  const session = await (await admin.request.get("/api/admin/session")).json();
  const headers = {
    Origin: "http://localhost:5174",
    "X-CSRF-Token": session.csrf,
  };
  const response = await admin.request.post("/api/admin/approved-users", {
    headers,
    data: { email },
  });
  expect(response.status()).toBe(201);
  const approval = await response.json();
  await mockGoogleScript(page);
  await page.goto("/login");
  await page.evaluate((value) => {
    (window as any).testGoogleEmail = value;
  }, email);
  await page.getByRole("button", { name: "Continue with Google" }).click();
  await expect(page).toHaveURL(/\/dashboard$/);
  await page.getByRole("link", { name: "Upload meeting", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Bring your recording." }),
  ).toBeVisible();
  return async () => {
    const result = await admin.request.delete(
      `/api/admin/approved-users/${approval.id}`,
      { headers },
    );
    expect(result.status()).toBe(200);
  };
}

test("idle revoked user loses the workspace, acknowledges approval message and returns home", async ({
  page,
  browser,
  baseURL,
}) => {
  const admin = await browser.newContext({ baseURL });
  try {
    const revoke = await prepare(page, admin, "revoked-idle@example.edu");
    await revoke();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible({ timeout: 10000 });
    await expect(dialog).toContainText("You are not approved");
    await expect(page.locator(".app-shell")).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "Create meeting minutes" }),
    ).toHaveCount(0);
    await expect(page).toHaveURL(/\/upload$/);
    await page.keyboard.press("Escape");
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: "OK", exact: true }).click();
    await expect(page).toHaveURL(baseURL + "/");
    await expect(
      page.getByRole("link", { name: "Sign in", exact: true }),
    ).toBeVisible();
    // A direct private URL must show no protected workspace, even after a reload.
    await page.goto("/upload");
    await expect(page.getByRole("dialog")).toContainText(
      "You are not approved",
    );
    await expect(page.locator(".app-shell")).toHaveCount(0);
    expect((await page.request.get("/api/config")).status()).toBe(403);
    await page.getByRole("button", { name: "OK", exact: true }).click();
    await expect(page).toHaveURL(baseURL + "/");
  } finally {
    await admin.close();
  }
});

test("a revoked upload response clears stale authentication and shows the approval dialog", async ({
  page,
  browser,
  baseURL,
}) => {
  const admin = await browser.newContext({ baseURL });
  try {
    const revoke = await prepare(page, admin, "revoked-upload@example.edu");
    // Hold periodic session checks so this case specifically exercises XMLHttpRequest handling.
    await page.route("**/api/auth/me", (route) => route.abort());
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
      .setInputFiles({
        name: "revoked-upload.wav",
        mimeType: "audio/wav",
        buffer: wav,
      });
    await page.getByLabel("I have participant consent").check();
    await revoke();
    const result = page.waitForResponse((response) =>
      response.url().endsWith("/api/meetings/upload"),
    );
    await page.getByRole("button", { name: "Create meeting minutes" }).click();
    expect((await result).status()).toBe(403);
    await expect(page.getByRole("dialog")).toContainText(
      "You are not approved",
    );
    await expect(page.locator(".app-shell")).toHaveCount(0);
    await page.getByRole("button", { name: "OK", exact: true }).click();
    await expect(page).toHaveURL(baseURL + "/");
  } finally {
    await admin.close();
  }
});
