import { test, expect } from "@playwright/test";
import { open, unlink } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { randomUUID } from "node:crypto";

test("meeting picker accepts a file larger than 250 MB when no size cap is configured", async ({
  page,
  context,
}) => {
  const path = join(tmpdir(), `sumx-large-upload-${randomUUID()}.wav`);
  const file = await open(path, "w");
  try {
    await file.truncate(251 * 1024 * 1024);
  } finally {
    await file.close();
  }
  try {
    await context.addCookies([
      {
        name: "sumx_session",
        value: "browser-test-token",
        domain: "localhost",
        path: "/",
      },
    ]);
    await page.goto("/upload");
    await expect(page.getByText(/No file size limit/)).toBeVisible();
    await page.getByLabel("Choose a meeting file").setInputFiles(path);
    await expect(page.locator(".file-preview")).toContainText("251.0 MB");
    await expect(page.getByText(/Choose a file smaller than/)).toHaveCount(0);
    await page.getByLabel("I have participant consent").check();
    await expect(
      page.getByRole("button", { name: "Create meeting minutes" }),
    ).toBeEnabled();
  } finally {
    await page.goto("/");
    await unlink(path);
  }
});
