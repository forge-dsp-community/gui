// The car page of the Android app (#29) on a 7" head unit: 1024 x 600, touch skin (demo).
import { expect, test } from "@playwright/test";

test.use({ viewport: { width: 1024, height: 600 } });

test("car page: fits 1024x600, mute, preset, source", async ({ page }) => {
  await page.goto("./?demo&page=car&skin=touch");
  const car = page.getByTestId("car");
  await expect(car).toBeVisible({ timeout: 30_000 });
  // no horizontal scrolling at the head unit's width
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(1024);
  // touch targets
  const out1 = car.getByRole("button", { name: /^1 / });
  expect((await out1.boundingBox())!.height).toBeGreaterThanOrEqual(44);
  await expect(out1).toHaveAttribute("aria-pressed", "false");
  await out1.click();
  await expect(out1).toHaveAttribute("aria-pressed", "true");
  await car.getByRole("button", { name: "Alle stumm" }).click();
  await expect(car.getByRole("button", { name: "Ton an" })).toBeVisible();
  await car.getByRole("button", { name: "Ton an" }).click();
  await expect(out1).toHaveAttribute("aria-pressed", "true");          // back as before
  await car.getByRole("button", { name: "3", exact: true }).click();
  await expect(car.getByRole("button", { name: "3", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(car).toContainText("km/h");                              // GALA of the demo
  await car.getByRole("button", { name: "Alle Einstellungen" }).click();
  await expect(page.getByTestId("car")).toHaveCount(0);
});
