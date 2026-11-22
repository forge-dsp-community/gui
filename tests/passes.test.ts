import { describe, expect, it } from "vitest";
import type { Biquad } from "../src/lib/device/model";
import { blockWrites, classify, passBlocks, sectionQs, sections } from "../src/lib/dsp/passes";
import { passes } from "../src/lib/dsp/crossover";

const on = (type: number, f: number, q = 707): Biquad => ({ type, f, gain: 0, q, b: 0, flags: 0x80 });
const r3 = (qs: number[]) => qs.map((q) => Math.round(q * 1000) / 1000);

describe("pass blocks (gui#47)", () => {
  it("Q of the sections: Butterworth and Linkwitz-Riley", () => {
    expect(r3(sectionQs("bw", 2))).toEqual([0.707]);
    expect(r3(sectionQs("bw", 3))).toEqual([0, 1]);
    expect(r3(sectionQs("bw", 4))).toEqual([1.307, 0.541]);
    expect(r3(sectionQs("lr", 2))).toEqual([0.5]);
    expect(r3(sectionQs("lr", 4))).toEqual([0.707, 0.707]);
    expect(r3(sectionQs("lr", 6))).toEqual([0.5, 1, 1]);
    expect(r3(sectionQs("lr", 8))).toEqual([1.307, 0.541, 1.307, 0.541]);
  });
  it("sections in the firmware's types: 1st order, Butterworth, Linkwitz, else free Q", () => {
    expect(sections("bw", 1, "hp", 80).map((b) => b.type)).toEqual([8]);
    expect(sections("lr", 4, "lp", 3000).map((b) => [b.type, b.f])).toEqual([[3, 3000], [3, 3000]]);
    expect(sections("bw", 4, "hp", 80).map((b) => [b.type, b.q])).toEqual([[13, 1307], [13, 541]]);
    expect(sections("lr", 6, "hp", 80).map((b) => [b.type, b.q])).toEqual([[11, 707], [13, 1000], [13, 1000]]);
  });
  it("recognises what the crossover wizard writes", () => {
    expect(classify(passes("lr24", 80, true))).toEqual({ char: "lr", order: 4 });
    expect(classify(passes("bw24", 80, false))).toEqual({ char: "bw", order: 4 });
    expect(classify(passes("lr48", 80, true))).toEqual({ char: "lr", order: 8 });
    expect(classify(passes("bw6", 80, true))).toEqual({ char: "bw", order: 1 });
    expect(classify(passes("lr12", 80, true))).toEqual({ char: "lr", order: 2 });
    expect(classify([on(10, 80), on(9, 80)])).toBeNull();   // Bessel + Butterworth: own combination
    for (const c of ["bw", "lr"] as const)
      for (const n of c === "bw" ? [1, 2, 3, 4, 5, 6, 7, 8] : [2, 4, 6, 8])
        expect(classify(sections(c, n, "lp", 500))).toEqual({ char: c, order: n });
  });
  it("groups by direction, frequency and on/off", () => {
    const slots = [[1, on(1, 1000)], [2, on(9, 3200)], [3, on(15, 9000)], [4, on(9, 3200)], [5, on(3, 3200)],
      [6, { ...on(9, 3200), flags: 0 }]].map(([n, bq]) => ({ n: n as number, bq: bq as Biquad }));
    const b = passBlocks(slots);
    expect(b.map((x) => [x.dir, x.f, x.slots, x.kind])).toEqual([
      ["hp", 3200, [2, 4], { char: "lr", order: 4 }], ["lp", 3200, [5], { char: "bw", order: 2 }],
      ["hp", 3200, [6], { char: "bw", order: 2 }]]);
  });
  it("order changes reuse the block's slots, then free ones, and clear the rest", () => {
    const blk = { dir: "hp" as const, f: 80, slots: [2, 4], kind: { char: "lr" as const, order: 4 } };
    const up = blockWrites(blk, sections("lr", 8, "hp", 80), [3, 7, 9]);
    expect(up.writes.map(([n]) => n)).toEqual([2, 4, 3, 7]);
    expect(up.lost).toBe(0);
    const down = blockWrites(blk, sections("bw", 1, "hp", 80), [3]);
    expect(down.writes.map(([n, b]) => [n, b.type])).toEqual([[2, 8], [4, 0]]);
    expect(blockWrites(blk, sections("lr", 8, "hp", 80), [3]).lost).toBe(1);
  });
});
