import { expect, test } from "@playwright/test";

async function signIn(context: import("@playwright/test").BrowserContext) {
  await context.addCookies([
    {
      name: "sumx_session",
      value: "voice-browser-token",
      domain: "localhost",
      path: "/",
    },
  ]);
}

test("three modes stay independent through generation, switching, refresh and narration", async ({
  page,
  context,
}) => {
  await signIn(context);
  await page.goto("/history");
  await page
    .getByRole("link", { name: "Summary modes meeting", exact: true })
    .click();
  const mode = page.getByLabel("Summary detail");
  const generate = async (name: string) => {
    await mode.selectOption(name);
    await page
      .getByRole("button", { name: /^(Generate summary|Regenerate)$/ })
      .click();
    await expect(page.getByText(new RegExp(`${name} revision \\d`))).toBeVisible(
      { timeout: 20000 },
    );
  };
  for (const name of ["concise", "standard", "detailed"]) {
    await mode.selectOption(name);
    await expect(
      page.getByText(`No ${name} summary generated yet.`),
    ).toBeVisible();
  }
  await page.getByRole("tab", { name: "Recording & narration" }).click();
  await expect(page.getByLabel("Narration summary")).toBeDisabled();
  await expect(page.getByText("No generated summaries available.")).toHaveCount(
    1,
  );
  await page.getByRole("tab", { name: "Summary" }).click();

  await generate("concise");
  const concise = await page.getByText(/concise revision \d/).innerText();
  await mode.selectOption("standard");
  await expect(
    page.getByText("No standard summary generated yet."),
  ).toBeVisible();
  await expect(page.getByText(concise)).toHaveCount(0);

  await generate("standard");
  await generate("detailed");
  const detailed = await page.getByText(/detailed revision \d/).innerText();
  await mode.selectOption("standard");
  const standard = await page.getByText(/standard revision \d/).innerText();
  await generate("standard");
  await expect(page.getByText(standard, { exact: true })).toHaveCount(0);

  for (let round = 0; round < 2; round++) {
    await mode.selectOption("concise");
    await expect(page.getByText(concise, { exact: true })).toBeVisible();
    await mode.selectOption("detailed");
    await expect(page.getByText(detailed, { exact: true })).toBeVisible();
  }

  await page.reload();
  await mode.selectOption("concise");
  await expect(page.getByText(concise, { exact: true })).toBeVisible();

  await page.getByRole("tab", { name: "Recording & narration" }).click();
  const source = page.getByLabel("Narration summary");
  expect(
    await source
      .locator("option")
      .evaluateAll((options) =>
        options.map((option) => (option as HTMLOptionElement).value),
      ),
  ).toEqual(["concise", "standard", "detailed"]);
});

test("a slow response for one mode cannot replace the selected mode", async ({
  page,
  context,
}) => {
  await signIn(context);
  await page.goto("/history");
  await page
    .getByRole("link", { name: "Summary modes meeting", exact: true })
    .click();
  const mode = page.getByLabel("Summary detail");
  await mode.selectOption("detailed");
  await expect(page.getByText(/detailed revision \d/)).toBeVisible();
  await page.reload();
  await page.route("**/summaries/concise", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 1500));
    await route.continue();
  });
  await mode.selectOption("concise");
  await mode.selectOption("detailed");
  await expect(page.getByText(/detailed revision \d/)).toBeVisible();
  await page.waitForTimeout(2000);
  await expect(page.getByText(/detailed revision \d/)).toBeVisible();
  await expect(page.getByText(/concise revision \d/)).toHaveCount(0);
});
