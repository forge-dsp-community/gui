// High and low passes of higher order as one filter block (#47). The firmware only knows single
// biquads (1st and 2nd order); a 24 dB/oct Linkwitz-Riley is two Butterworth 2nd order at the same
// frequency. The GUI groups the passes of a channel by direction, frequency and on/off and
// recognises Butterworth (order 1…8) and Linkwitz-Riley (2, 4, 6, 8) in them. Nothing is stored
// on the device: the crossover wizard (#25) and older tunings are recognised the same way.
import type { Biquad } from "../device/model";

export type Dir = "lp" | "hp";
export type Char = "bw" | "lr";
export const BW_ORDERS = [1, 2, 3, 4, 5, 6, 7, 8];
export const LR_ORDERS = [2, 4, 6, 8];
export const ordersOf = (c: Char) => (c === "bw" ? BW_ORDERS : LR_ORDERS);

const Q_BW = Math.SQRT1_2, Q_BESSEL = 0.5773502691896257, Q_LR = 0.5, Q_CHEB = 1.0;

/** direction of a pass type (2…7 low pass, 8…13 high pass), null for anything else */
export const dirOf = (type: number): Dir | null => (type >= 2 && type <= 7 ? "lp" : type >= 8 && type <= 13 ? "hp" : null);

/** Q of the sections of a Butterworth of order n (0 = 1st order section) */
function bwQs(n: number): number[] {
  const q: number[] = n % 2 ? [0] : [];
  for (let k = 1; k <= Math.floor(n / 2); k++) q.push(1 / (2 * Math.sin(((2 * k - 1) * Math.PI) / (2 * n))));
  return q;
}

/** Q of the sections: Linkwitz-Riley of order 2m = Butterworth of order m twice; two 1st order
 * sections are one 2nd order with Q 0.5 (LR 2 itself, the odd half of LR 6) */
export function sectionQs(c: Char, n: number): number[] {
  if (c === "bw") return bwQs(n);
  const half = bwQs(n / 2);
  return half[0] === 0 ? [Q_LR, ...half.slice(1), ...half.slice(1)] : [...half, ...half];
}

/** Q of one stored biquad (0 = 1st order), null for no pass */
function qOf(b: Biquad): number | null {
  switch (b.type) {
    case 2: case 8: return 0;
    case 3: case 9: return Q_BW;
    case 4: case 10: return Q_BESSEL;
    case 5: case 11: return Q_LR;
    case 6: case 12: return Q_CHEB;
    case 7: case 13: return b.q / 1000;
  }
  return null;
}

/** the biquads of a block, in the types the firmware and the wizard use (Butterworth/Linkwitz
 * where the Q is theirs, else free Q) */
export function sections(c: Char, n: number, dir: Dir, f: number, flags = 0x80): Biquad[] {
  const off = dir === "hp" ? 6 : 0;
  return sectionQs(c, n).map((q) => {
    const type = q === 0 ? 2 : Math.abs(q - Q_BW) < 1e-6 ? 3 : Math.abs(q - Q_LR) < 1e-6 ? 5 : 7;
    return { type: type + off, f, gain: 0, q: type === 7 ? Math.round(q * 1000) : 707, b: 0, flags };
  });
}

/** Butterworth or Linkwitz-Riley of which order these biquads are (any order of the slots) */
export function classify(bqs: Biquad[]): { char: Char; order: number } | null {
  const qs = bqs.map(qOf);
  if (qs.some((q) => q === null)) return null;
  const got = (qs as number[]).slice().sort((a, b) => a - b);
  const same = (want: number[]) => {
    const w = want.slice().sort((a, b) => a - b);
    return w.length === got.length && w.every((q, i) => Math.abs(q - got[i]) < 0.006);
  };
  // a single 2nd order Butterworth is "Butterworth 12", two of them Linkwitz-Riley 24
  for (const char of ["bw", "lr"] as Char[])
    for (const order of ordersOf(char)) if (same(sectionQs(char, order))) return { char, order };
  return null;
}

export interface PassBlock {
  dir: Dir;
  f: number;
  /** slot numbers, ascending; the first one stands for the block */
  slots: number[];
  /** recognised characteristic, null = another combination (Bessel, Chebyshev, own Q …) */
  kind: { char: Char; order: number } | null;
}

/** the pass blocks of a channel: passes with the same direction, frequency and on/off together */
export function passBlocks(slots: { n: number; bq: Biquad }[]): PassBlock[] {
  const by = new Map<string, { n: number; bq: Biquad }[]>();
  for (const s of slots) {
    const dir = dirOf(s.bq.type);
    if (!dir) continue;
    const key = `${dir}:${s.bq.f}:${s.bq.flags & 0x80}`;
    by.set(key, [...(by.get(key) ?? []), s]);
  }
  return [...by.values()].map((g) => {
    const sorted = g.slice().sort((a, b) => a.n - b.n);
    return { dir: dirOf(sorted[0].bq.type)!, f: sorted[0].bq.f, slots: sorted.map((s) => s.n), kind: classify(sorted.map((s) => s.bq)) };
  });
}

/** the order of a block (sum of its sections) */
export const orderOf = (bqs: Biquad[]) => bqs.reduce((n, b) => n + (qOf(b) === 0 ? 1 : 2), 0);

/**
 * The writes that turn a block into another characteristic or order: its slots first, then free
 * ones (`free`, ascending); slots it no longer needs are cleared. lost = sections that do not fit.
 */
export function blockWrites(block: PassBlock, want: Biquad[], free: number[]): { writes: [number, Biquad][]; lost: number } {
  const room = [...block.slots, ...free.filter((n) => !block.slots.includes(n))];
  const writes: [number, Biquad][] = want.slice(0, room.length).map((bq, i) => [room[i], bq]);
  for (const n of block.slots.slice(want.length)) writes.push([n, { type: 0, f: 0, gain: 0, q: 0, b: 0, flags: 0 }]);
  return { writes, lost: Math.max(0, want.length - room.length) };
}
