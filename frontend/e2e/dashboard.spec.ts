import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test("demo dashboard, guarded paper lifecycle and all seven pages", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Overview", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("DEMO DATA", { exact: true })).toBeVisible();
  const accessibility = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect(accessibility.violations).toEqual([]);
  await expect(
    page.getByText("Capital committed", { exact: true }),
  ).toBeVisible();
  expect(
    (
      await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
        .analyze()
    ).violations,
  ).toEqual([]);
  await page.getByRole("button", { name: "Operator login" }).click();
  await page
    .getByLabel("Password", { exact: true })
    .fill(process.env.E2E_PASSWORD ?? "");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "Enable paper simulation" }).click();
  await page.getByRole("button", { name: "Enable paper only" }).click();
  await expect(page.getByText("PAPER ENABLED", { exact: true })).toBeVisible();
  await page
    .getByRole("button", { name: "Opportunities", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: /atlanta falcons/i }).first(),
  ).toBeVisible({ timeout: 30000 });
  await page
    .getByRole("button", { name: /atlanta falcons/i })
    .first()
    .click();
  await expect(
    page.getByText("Unused Kalshi allocation", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Conditional gross payout", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Close dialog" }).click();
  await page
    .getByRole("button", { name: "Paper trading", exact: true })
    .click();
  await expect(page.getByText("HEDGED", { exact: true }).first()).toBeVisible({
    timeout: 30000,
  });
  await page
    .getByRole("button", { name: "Market matching", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Contract graph" }),
  ).toBeVisible();
  await page.getByLabel("Match status").selectOption("approved");
  await page
    .getByRole("button", { name: /Inspect contract and rule evidence/ })
    .first()
    .click();
  await page
    .getByLabel("Review note")
    .fill("E2E operator review of synthetic fixture");
  await page.getByRole("button", { name: "Approve verified pair" }).click();
  await page.getByText("Review audit history", { exact: true }).click();
  await expect(
    page.locator("pre").filter({
      hasText: "E2E operator review of synthetic fixture",
    }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Close dialog" }).click();
  await page.getByRole("button", { name: "Analytics", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Expected vs. simulated result" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "System health", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Worker leases and heartbeats" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByLabel("Minimum net profit (USD)").fill("2");
  await page
    .getByRole("button", { name: "Save audited risk settings" })
    .click();
  await expect(page.getByLabel("Minimum net profit (USD)")).toHaveValue("2");
  const resumePaper = page.getByRole("button", {
    name: "Enable paper simulation",
  });
  if (await resumePaper.isVisible()) {
    await expect(page.getByText("STOPPED", { exact: true })).toBeVisible();
    await resumePaper.click();
    await page.getByRole("button", { name: "Enable paper only" }).click();
    await expect(
      page.getByText("PAPER ENABLED", { exact: true }),
    ).toBeVisible();
  }
  await page
    .getByRole("button", { name: "Activate paper kill switch" })
    .click();
  await expect(page.getByText("STOPPED", { exact: true })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("button", { name: "Overview", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Overview", exact: true }),
  ).toBeVisible();
});
