import type { Step } from "./update";

/**
 * Progress, result and backup of the last firmware update (#41). Outside the page: after the update
 * the GUI reads the Forge again and shows its reading screen, which unmounts the firmware page; the
 * result and, in the Android app, the only way to save the backup (no save dialog during the update)
 * must survive that.
 */
export const upd = $state({
  steps: [] as { step: Step; done?: number; total?: number }[],
  result: null as { ok: boolean; text: string; touched?: boolean; complete?: boolean } | null,
  saved: null as { flash: Uint8Array; eeprom: Uint8Array; flashName: string; eepromName: string } | null,
  sdNote: "",
});
