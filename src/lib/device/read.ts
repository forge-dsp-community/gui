import type { ForgeClient } from "../protocol/client";
import type { Answer } from "../protocol/answer";
import { applyAnswer, emptyState, type DeviceState } from "./model";

export interface ReadProgress {
  done: number;
  total: number;
  /** register being read */
  current: string;
}

/** read once after connecting; `port` = only the C port answers these */
const STEPS: { reg: string; port?: boolean; slow?: boolean }[] = [
  { reg: "system.key.info" },
  { reg: "system.firmware" },
  { reg: "system.firmware.port", port: true },
  { reg: "system.info" },
  { reg: "system.key.info.list", port: true },
  { reg: "system.feature.attributes" },
  { reg: "system.limits", port: true },
  { reg: "system.gui.panes" },
  { reg: "gain.out.scale" },
  { reg: "gain.in.scale" },
  { reg: "gain.spdif.scale" },
  { reg: "mixer.scale" },
  { reg: "gain.out" },
  { reg: "gain.in" },
  { reg: "gain.spdif" },
  { reg: "mute.out" },
  { reg: "phase.out" },
  { reg: "delay" },
  { reg: "delay.in" },
  { reg: "mixer.position" },
  { reg: "mixer.in", port: true },
  { reg: "mixer.in.max", port: true },
  { reg: "preset.loaded" },
  { reg: "preset.select" },
  { reg: "system.channel.name.*" },
  { reg: "system.channel.in.*", port: true },
  { reg: "system.errors" },
  { reg: "system.temperature.celsius" },
  { reg: "biquad.clickless", port: true },
  { reg: "system.supply", port: true },
  { reg: "system.startup.time", port: true },
  { reg: "system.uptime", port: true },
  { reg: "system.shutdown.hold", port: true },
  { reg: "system.spdif.out", port: true },
  { reg: "system.vu", port: true },
  { reg: "system.vu.out", port: true },
  { reg: "system.dsp.limiter", port: true },
  { reg: "system.dsp.limiter.attack", port: true },
  { reg: "system.dsp.limiter.release", port: true },
  { reg: "modc.version.attributes" },
  { reg: "modc.*" },
  { reg: "mixer.*", slow: true },
  { reg: "system.biquads", port: true },
  { reg: "biquad.*", slow: true },
];

/** lists whose entries are read one by one: system.feature.attributes=["fr","hw"] -> ….fr, ….hw */
const SUBLISTS = ["system.feature.attributes", "system.gui.panes", "modc.version.attributes", "system.limits"];

/**
 * Reads the complete state of one device. The protocol is slow (38400 Bd, the mixer and biquad
 * lists take seconds), so this runs once; afterwards `applyAnswer` keeps the state current.
 * Registers the firmware does not know are skipped.
 */
export async function readDevice(
  client: ForgeClient,
  onProgress: (p: ReadProgress) => void = () => {},
): Promise<DeviceState> {
  const state = emptyState();
  const steps = [...STEPS];
  const skipped: { reg: string; port?: boolean; slow?: boolean }[] = [];
  let portRefused = false;   // original firmware: it answered "unknown command"
  const missed: { reg: string; port?: boolean; slow?: boolean }[] = [];   // port registers without an answer
  for (let i = 0; i < steps.length; i++) {
    const { reg, port } = steps[i];
    onProgress({ done: i, total: steps.length, current: reg });
    // original firmware (answered system.firmware, but not system.firmware.port): not asked
    if (port && reg !== "system.firmware.port" && state.identity.port === null) { skipped.push(steps[i]); continue; }
    let answers: Answer[] | null = null;
    // a lost answer (38400 Bd, a device busy with a preset load) is asked once more; an error answer
    // ("unknown command") is an answer and ends it
    // port firmware with forge-dsp-firmware#64: only the biquads in use (most places are empty)
    if (reg === "biquad.*" && state.identity.port !== null) {
      const used = await client.get("biquad.*.used", { until: "biquad.used", timeoutMs: 3000 }).catch(() => null);
      if (used?.some((a) => a.register === "biquad.used")) answers = used;
    }
    // the identity decides which registers follow (port firmware or original): more time for it, a slow
    // line (Android VM, a Forge busy after a restart) answered it after the 1.5 s and every port register
    // was skipped (#42)
    const ident = reg === "system.firmware" || reg === "system.firmware.port";
    for (let t = 0; t < (ident ? 3 : 2) && !answers; t++) {
      try {
        answers = await client.get(reg);
      } catch { /* no answer */ }
    }
    if (reg === "system.firmware.port" && answers?.some((a) => a.kind === "error")) portRefused = true;
    if (!answers) {   // optional register, or an answer lost while connecting: a port register is asked once more at the end
      if (port) missed.push(steps[i]);
      continue;
    }
    for (const a of answers) {
      applyAnswer(state, a);
      if (a.kind === "get" && SUBLISTS.includes(a.register) && Array.isArray(a.value))
        for (const name of a.value) steps.splice(i + 1, 0, { reg: `${a.register}.${name}` });
    }
  }
  // the port identity got lost while connecting (the Android service and the GUI set the rate at the same
  // time; #42): asked once more at the end, and when it is a port firmware after all, the port registers
  // that were left out are read now
  if (state.identity.port === null && skipped.length && state.identity.firmware && !portRefused) {
    for (let t = 0; t < 2 && state.identity.port === null; t++) {
      const a = await client.get("system.firmware.port").catch(() => null);
      if (a?.some((x) => x.kind === "error")) break;   // original firmware: right to skip them
      for (const x of a ?? []) applyAnswer(state, x);
    }
  }
  // port registers left out or without an answer (#42: the first connect after installing the Android app
  // lost answers while the app restarted): once more, now that the identity is known
  if (state.identity.port !== null && !portRefused) {
    for (const st of [...skipped, ...missed]) {
      onProgress({ done: steps.length, total: steps.length, current: st.reg });
      const a = await client.get(st.reg).catch(() => null);
      for (const x of a ?? []) applyAnswer(state, x);
    }
  }
  onProgress({ done: steps.length, total: steps.length, current: "" });
  return state;
}

/** state as a file: readable JSON with a format version */
export function exportState(state: DeviceState): string {
  return JSON.stringify({ format: "forge-dsp-gui-ng/state", version: 1, state }, null, 1);
}

/** values that change on their own: read again from time to time (`port` as above) */
export const STATUS_REGS: { reg: string; port?: boolean }[] = [
  { reg: "system.temperature.celsius" }, { reg: "system.errors" }, { reg: "system.supply", port: true },
  { reg: "system.uptime", port: true },
];
