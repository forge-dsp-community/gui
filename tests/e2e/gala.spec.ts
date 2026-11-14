// GALA (forge-dsp-firmware#71) inside its control block: fields, curve, live speed and the test
// speed (demo: answers system.kmh like the firmware for the example block).
import { expect, test } from "@playwright/test";

test("GALA block: curve, live speed, test speed", async ({ page }) => {
  await page.goto("./?demo&page=modc&block=6");
  const g = page.getByTestId("gala");
  await expect(g).toBeVisible({ timeout: 30_000 });
  await expect(g).toContainText("Ab 30 km/h +1 dB je 10 km/h, höchstens +6 dB. Grenze bei 90 km/h.");
  await expect(page.getByLabel("Geschwindigkeit von")).toHaveValue("0");
  await expect(g.locator("path.vol")).toBeVisible();
  await expect(g.locator("path.bass")).toBeVisible();
  await expect(g.locator(".big")).toContainText("km/h", { timeout: 5000 });
  await g.getByLabel("Testgeschwindigkeit").fill("80");
  await g.getByRole("button", { name: "Senden" }).click();
  await expect(g.locator(".big")).toContainText("80 km/h", { timeout: 6000 });
  await expect(g).toContainText("+5 dB");
  await expect(g).toContainText("+6 dB");
  await g.getByRole("button", { name: "Stoppen" }).click();
});
