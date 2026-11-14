import { describe, expect, it } from "vitest";
import { applyAnswer, emptyModc, emptyState, galaBoost, galaFullAt, galaParams } from "../src/lib/device";
import { parseAnswer } from "../src/lib/protocol";

// the block of the firmware's example (docs/port-changes.md, GALA): outputs 1-4, command,
// from 30 km/h 1 dB per 10 km/h up to 6 dB, bass +2 dB per 10 km/h up to 6 dB
const EXAMPLE = { ...emptyModc(), enable: 1, type: 110, flags: [0x0f, 0, 30, 12], events: [10, 20, 0, 0], sets: [0, 0, 0, 0] };

describe("GALA (forge-dsp-firmware#71)", () => {
  it("defaults as the firmware", () => {
    const p = galaParams({ ...emptyModc(), type: 110 });
    expect(p).toEqual({ outs: 0, source: 0, start: 0, volSlope: 1, volMax: 6, bassSlope: 0, bassMax: 6, ramp: 1, pulses: 4000, timeout: 3 });
    expect(galaParams({ ...emptyModc(), type: 110, sets: [0x10, 0x0e, 5, 0] }).pulses).toBe(3600);
  });
  it("boost at a speed: the example of the documentation", () => {
    const p = galaParams(EXAMPLE);
    expect(galaBoost(p, 20)).toEqual({ vol: 0, bass: 0 });
    expect(galaBoost(p, 80)).toEqual({ vol: 5, bass: 6 });
    expect(galaBoost(p, 200)).toEqual({ vol: 6, bass: 6 });
    expect(galaBoost(p, 54)).toEqual({ vol: 2.5, bass: 4.8 });   // 2.4 dB -> nearest 0.5-dB step
    expect(galaFullAt(p)).toBe(90);
    expect(galaBoost({ ...p, outs: 0 }, 80).vol).toBe(0);         // no outputs: nothing acts
  });
  it("reads system.kmh", () => {
    const s = emptyState();
    expect(applyAnswer(s, parseAnswer("<.1 system.kmh=[80,50,60,0]")!)).toBe(true);
    expect(s.speed).toEqual({ kmh: 80, vol: 50, bass: 60, source: 0 });
    expect(applyAnswer(s, parseAnswer("<.1 system.kmh=[1,2]")!)).toBe(false);
  });
});
