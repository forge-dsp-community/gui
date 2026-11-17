/**
 * State of the factory reset dialog (#40). Outside the component: the reset ends with the GUI
 * reading the Forge again (reading screen), which unmounts the device page; result, calibration
 * comparison and the offer to write the calibration back must survive that.
 */
export type Cal = { fine: number | null; supply: number[] | null; vcc: number | null };
export const fr = $state({
  phase: "idle" as "idle" | "confirm" | "running" | "done",
  before: null as Cal | null,
  after: null as Cal | null,
  err: "",
  waited: 0,
  restored: "",
});
