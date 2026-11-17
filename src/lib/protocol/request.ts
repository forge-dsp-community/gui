import { checksumHex } from "./checksum";

/** Receiver: device 1..6 in the chain, "a" all, "m" master, "s" slave, "t" this device. */
export type Target = 1 | 2 | 3 | 4 | 5 | 6 | "a" | "m" | "s" | "t";

export type Value = number | string | Value[];

/** Encode a value as the firmware expects it: strings quoted, arrays in [a,b,c]. */
export function encodeValue(v: Value): string {
  if (Array.isArray(v)) return `[${v.map(encodeValue).join(",")}]`;
  // the firmware knows no escape: a quote inside would end the string early (#39)
  if (typeof v === "string") {
    if (v.includes('"')) throw new Error('a string value must not contain "');
    return `"${v}"`;
  }
  return String(v);
}

/** the command buffer of the firmware takes 127 bytes per line including CR LF (iface.c, iface_copy2buffer);
 *  a longer line arrives cut and is run without an error (#39) */
export const MAX_LINE = 127;
export class LineTooLong extends Error {
  constructor(public readonly length: number) { super(`line too long for the Forge: ${length} > ${MAX_LINE} bytes`); }
}
function checkLength(l: string): string {
  const n = new TextEncoder().encode(l).length;
  if (n > MAX_LINE) throw new LineTooLong(n);
  return l;
}

export interface LineOptions {
  target?: Target;
  /** append ;XX (the firmware checks it when present) */
  checksum?: boolean;
  /** how long the client waits for the answer (default of the client: 1.5 s); with `until` per line */
  timeoutMs?: number;
  /** a wildcard answer ends with this register (not after a quiet time): answers with pauses */
  until?: string;
}

function line(cmd: "get" | "set", reg: string, value: Value | undefined, o: LineOptions): string {
  if (!/^[a-z0-9.*]+$/i.test(reg)) throw new Error(`invalid register: ${reg}`);
  const body = value === undefined ? reg : `${reg}=${encodeValue(value)}`;
  if (/[\r\n;]/.test(body)) throw new Error("value must not contain CR, LF or ';'");
  const cs = o.checksum ? `;${checksumHex(body)}` : "";
  return checkLength(`${cmd}.${o.target ?? 1} ${body}${cs}\r\n`);
}

export const getLine = (reg: string, o: LineOptions = {}) => line("get", reg, undefined, o);
export const setLine = (reg: string, value: Value, o: LineOptions = {}) => line("set", reg, value, o);

/** set with the value text as it is (a line of a tuning file: lists with hex fields, quoted names) */
export function setLineRaw(reg: string, valueText: string, o: LineOptions = {}): string {
  if (!/^[a-z0-9.*]+$/i.test(reg)) throw new Error(`invalid register: ${reg}`);
  const body = `${reg}=${valueText}`;
  if (/[\r\n;]/.test(body)) throw new Error("value must not contain CR, LF or ';'");
  return checkLength(`set.${o.target ?? 1} ${body}${o.checksum ? `;${checksumHex(body)}` : ""}\r\n`);
}
