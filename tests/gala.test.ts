import { describe, expect, it } from "vitest";
import { applyAnswer, emptyModc, emptyState, galaBoost, galaFullAt, galaParams, sumTargets, typeFields, type Field } from "../src/lib/device";
import { parseAnswer } from "../src/lib/protocol";

// the block of the firmware's example (docs/port-changes.md, GALA): outputs 1-4, command,
// from 30 km/h 1 dB per 10 km/h up to 6 dB, bass +2 dB per 10 km/h up to 6 dB
const EXAMPLE = { ...emptyModc(), enable: 1, type: 110, flags: [0x0f, 0, 30, 12], events: [10, 20, 0, 0], sets: [0, 0, 0, 0] };

describe("GALA (forge-dsp-firmware#71)", () => {
  it("defaults as the firmware", () => {
    const p = galaParams({ ...emptyModc(), type: 110 });
    expect(p).toEqual({ outs: 0, sums: 0, source: 0, start: 0, volSlope: 1, volMax: 6, bassSlope: 0, bassMax: 6, ramp: 1, pulses: 4000, timeout: 3 });
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
    expect(galaBoost({ ...p, outs: 0, sums: 1 }, 80).vol).toBe(5); // sum A only
  });
  it("sums A/B: sets 4, bits 0/1", () => {
    expect(galaParams({ ...EXAMPLE, sets: [0, 0, 0, 0xfe] }).sums).toBe(2);
  });
});

describe("sums A/B as targets of GALA and the volume block", () => {
  const sumField = (f: Field[] | undefined, of: string, i: number) => f?.find((x) => x.kind === "bits" && x.of === of && x.i === i);
  it("only with a firmware that has GALA version 21", () => {
    const s = emptyState();
    expect(sumTargets(s)).toBe(false);
    expect(sumField(typeFields(s, 110), "sets", 4)).toBeUndefined();
    expect(sumField(typeFields(s, 16), "flags", 2)).toBeUndefined();
    s.modcVersions[110 & 0x3f] = 20;
    expect(sumTargets(s)).toBe(false);
    s.modcVersions[110 & 0x3f] = 21;
    expect(sumTargets(s)).toBe(true);
    const g = typeFields(s, 110)!;
    expect(g[1]).toMatchObject({ kind: "bits", of: "sets", i: 4 });        // right after the outputs
    const v = typeFields(s, 16)!;
    expect(v.filter((x) => x.kind === "bits" && x.of === "flags" && x.i === 2)
      .map((x) => (x as Extract<Field, { kind: "bits" }>).bits.findIndex((l) => l !== ""))).toEqual([0, 2, 3]);
    expect(v[1]).toMatchObject({ of: "flags", i: 2 });                     // after "active for"
    expect(typeFields(s, 108)).toEqual(typeFields(emptyState(), 108));      // other types unchanged
  });
  it("reads system.kmh", () => {
    const s = emptyState();
    expect(applyAnswer(s, parseAnswer("<.1 system.kmh=[80,50,60,0]")!)).toBe(true);
    expect(s.speed).toEqual({ kmh: 80, vol: 50, bass: 60, source: 0 });
    expect(applyAnswer(s, parseAnswer("<.1 system.kmh=[1,2]")!)).toBe(false);
  });
});
