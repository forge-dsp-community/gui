// GALA (forge-dsp-firmware#71): speed-dependent volume and loudness, MODC type 110. The fields with
// the defaults of the firmware (src/modc.c modc_gala, src/gala.c), and the boost at a speed.
import type { Modc } from "./model";
import { wordGet } from "./modc";

export const GALA_TYPE = 110;
/** system.kmh: source when no GALA block is on */
export const GALA_NONE = 255;

export interface GalaParams {
  /** outputs (bit 0 = output 1) */
  outs: number;
  /** sums A/B (bit 0/1, sets 4, firmware with GALA version 21) */
  sums: number;
  /** 0 = command, 1..3 = ground input Imp 1..3, 4..6 = switch 1..3 */
  source: number;
  start: number;
  /** dB per 10 km/h */
  volSlope: number;
  volMax: number;
  bassSlope: number;
  bassMax: number;
  /** dB/s */
  ramp: number;
  pulses: number;
  /** s */
  timeout: number;
}

export function galaParams(m: Modc): GalaParams {
  const or = (v: number, d: number) => v || d;
  return {
    outs: m.flags[0], sums: (m.sets[3] ?? 0) & 3, source: m.flags[1], start: m.flags[2],
    volSlope: or(m.events[0], 10) / 10, volMax: or(m.flags[3], 12) / 2,
    bassSlope: m.events[1] / 10, bassMax: or(m.events[2], 12) / 2,
    ramp: or(m.events[3], 10) / 10,
    pulses: or(wordGet(m.sets, 1), 4000), timeout: or(m.sets[2], 30) / 10,
  };
}

/** the boost the firmware settles at for a steady speed: volume in 0.5 dB steps (nearest), bass in
 * 0.1 dB (the loudness block's grid); vol is 0 without outputs and sums */
export function galaBoost(p: GalaParams, kmh: number): { vol: number; bass: number } {
  const over = Math.max(0, kmh - p.start);
  const vol = Math.min(over * p.volSlope / 10, p.volMax);
  const bass = Math.min(over * p.bassSlope / 10, p.bassMax);
  return { vol: p.outs || p.sums ? Math.round(vol * 2) / 2 : 0, bass: Math.round(bass * 10) / 10 };
}

/** speed (km/h) from which the volume boost is at its limit, null = never (slope 0) */
export function galaFullAt(p: GalaParams): number | null {
  return p.volSlope > 0 ? Math.round(p.start + p.volMax / p.volSlope * 10) : null;
}
