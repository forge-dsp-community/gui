import { describe, expect, it } from "vitest";
import { emptyState, loudnessBusy, loudnessChannels, loudnessReserved, loudnessSlots, type DeviceState } from "../src/lib/device";

// forge-dsp-firmware#77: loudness takes the last two biquads of its channels, they must be free
function state(): DeviceState {
  const s = emptyState();
  for (const ch of [1, 2, 3, 4, 35, 36]) {
    s.biquads[ch] = {};
    for (let n = 1; n <= (ch > 30 ? 72 : 60); n++) s.biquads[ch][n] = { type: 0, f: 0, gain: 0, q: 0, b: 0, flags: 0 };
  }
  return s;
}

describe("loudness slots", () => {
  it("channels from flags 1", () => {
    expect(loudnessChannels(0x01)).toEqual([1, 2]);
    expect(loudnessChannels(0x12)).toEqual([3, 4, 35, 36]);
  });
  it("last two biquads per channel", () => {
    expect(loudnessSlots(state(), 0x10)).toEqual([{ ch: 35, n: 71 }, { ch: 35, n: 72 }, { ch: 36, n: 71 }, { ch: 36, n: 72 }]);
  });
  it("only active filters make a slot busy", () => {
    const s = state();
    s.biquads[1][60] = { type: 1, f: 3000, gain: 30, q: 2000, b: 0, flags: 0 };    // switched off: free
    s.biquads[2][59] = { type: 9, f: 80, gain: 0, q: 707, b: 0, flags: 128 };       // active
    expect(loudnessBusy(s, 0x01)).toEqual([{ ch: 2, n: 59 }]);
    expect(loudnessBusy(s, 0x02)).toEqual([]);
  });
  it("reserved: only switched-on blocks", () => {
    const s = state();
    s.modc[9] = { enable: 0, type: 108, flags: [0x01, 0, 0, 0], events: [0, 0, 0, 0], sets: [0, 0, 0, 0] };
    expect(loudnessReserved(s).size).toBe(0);
    s.modc[9].enable = 1;
    expect([...loudnessReserved(s)]).toEqual(["1.59", "1.60", "2.59", "2.60"]);
  });
});
