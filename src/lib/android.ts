// The Android app (android/, #29): the same GUI in a WebView. Chrome on Android has no Web Serial for
// USB-serial adapters, so the app lends its USB host access through a JavaScript interface:
//   window.ForgeAndroid  (Kotlin, android/.../ui/Bridge.kt)
//     present(): boolean                  a Forge (CP2102) is plugged in
//     open(id, baud)                      permission + open; answer through settle(id, error)
//     write(base64) / writeRaw(base64) / setBaud(baud) / close()   (writeRaw: bootloader, the service keeps quiet)
//     settings(): JSON / setSettings(JSON): error / refreshMaster()    the background service (volume, autostart)
//     save(id, base64, name, mime)        "save as" (Storage Access Framework); settle(id, error|"cancel")
//     update(): JSON / checkUpdate() / installUpdate()   self-update from the GitHub releases
//     keepScreenOn(on)                    screen stays on (firmware update, #41)
//     debuggable(): boolean               debug build (android/test-rig drives the GUI through window.__forgeRig)
//   window.__forgeAndroid  (this file)
//     data(base64)                        received bytes
//     gone()                              the Forge was unplugged
//     settle(id, error)                   end of an asynchronous call ("" = ok)
import { ByteQueue, LineSplitter, type Transport } from "./transport/types";

interface Bridge {
  present(): boolean;
  open(id: number, baud: number): void;
  write(b64: string): void;
  writeRaw(b64: string): void;
  settings(): string;
  setSettings(json: string): string;
  refreshMaster(): void;
  setBaud(baud: number): void;
  close(): void;
  save(id: number, b64: string, name: string, mime: string): void;
  update?(): string;
  checkUpdate?(): void;
  installUpdate?(): void;
  debuggable?(): boolean;
  keepScreenOn?(on: boolean): void;
  recover?(): void;
}

const bridge = (): Bridge | null => (typeof window !== "undefined" ? ((window as unknown as { ForgeAndroid?: Bridge }).ForgeAndroid ?? null) : null);

/** running inside the Android app */
export const isAndroid = () => bridge() !== null;
/** debug build of the app: the test rig (android/test-rig, #33) may drive the GUI */
export const androidDebuggable = () => !!bridge()?.debuggable?.();
/** screen stays on while true (no-op outside the app and with older apps) */
export const androidKeepScreenOn = (on: boolean) => bridge()?.keepScreenOn?.(on);
/** the service of the app looks for the rate of the Forge again (#42); false with older apps */
export const androidRecover = () => { const b = bridge(); if (!b?.recover) return false; b.recover(); return true; };

const waiting = new Map<number, (err: string) => void>();
let nextId = 1;
let onData: ((b: Uint8Array) => void) | null = null;
let attached: (() => void) | null = null;
/** the Forge was plugged in (USB_DEVICE_ATTACHED) */
export function onAndroidAttached(fn: () => void) { attached = fn; }
/** listeners of the open transport for "the Forge was unplugged" (#34) */
const goneFns: ((why: string) => void)[] = [];

const toB64 = (b: Uint8Array) => { let s = ""; for (let i = 0; i < b.length; i += 0x8000) s += String.fromCharCode(...b.subarray(i, i + 0x8000)); return btoa(s); };
const fromB64 = (s: string) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));

if (typeof window !== "undefined")
  (window as unknown as { __forgeAndroid: unknown }).__forgeAndroid = {
    data: (b64: string) => {
      // debug build: raw arrival for the test rig (#42)
      const tr = (globalThis as unknown as { __forgeTrace?: unknown[] }).__forgeTrace;
      if (tr) tr.push([Math.round(performance.now()), "data", `${b64.length} b64${onData ? "" : " (kein Empfänger)"}`]);
      onData?.(fromB64(b64));
    },
    attached: () => attached?.(),
    gone: () => { const f = [...goneFns]; goneFns.length = 0; f.forEach((x) => x("Forge unplugged")); },   // #34
    settle: (id: number, err: string) => { waiting.get(id)?.(err); waiting.delete(id); },
  };

function call(fn: (id: number) => void): Promise<string> {
  const id = nextId++;
  return new Promise((resolve) => { waiting.set(id, resolve); fn(id); });
}

export const androidPresent = () => bridge()?.present() ?? false;

/** the Forge at the USB port of the head unit; the line protocol and the bootloader's raw bytes */
export async function openAndroidSerial(baud = 38400): Promise<Transport> {
  const b = bridge();
  if (!b) throw new Error("no Android bridge");
  const err = await call((id) => b.open(id, baud));
  if (err) throw new Error(err);
  const listeners: ((l: string) => void)[] = [];
  const split = new LineSplitter((l) => listeners.forEach((f) => f(l)));
  const queue = new ByteQueue();
  const dec = new TextDecoder();
  onData = (bytes) => { queue.push(bytes); split.push(dec.decode(bytes, { stream: true })); };
  return {
    name: "USB (Android)",
    write: async (line) => b.write(toB64(new TextEncoder().encode(line))),
    onLine: (fn) => listeners.push(fn),
    onClose: (fn) => goneFns.push(fn),
    bytes: { write: async (d) => b.writeRaw(toB64(d)), read: (ms) => queue.read(ms), flush: () => queue.flush() },
    async setBaud(r) { queue.flush(); b.setBaud(r); },
    async close() { onData = null; b.close(); },
  };
}

/** how the Android volume acts on the Forge (android/…/service/VolumeControl.kt) */
export type VolumeMode = "OFF" | "MIRROR" | "MIRROR_FIXED" | "REMOTE" | "ABSOLUTE";
export interface AndroidSettings {
  volumeMode: VolumeMode;
  rangeDb: number;
  stepDb: number;
  autostart: boolean;
  /** state of the service (read only) */
  status?: string;
  master?: number[] | null;
  masterValue?: number | null;
  /** absolute volume on the USB device (#31): possible (system app), on, last error */
  absoluteAvailable?: boolean;
  absoluteActive?: boolean;
  absoluteError?: string;
}
export function androidSettings(): AndroidSettings | null {
  const b = bridge();
  if (!b) return null;
  try { return JSON.parse(b.settings()) as AndroidSettings; } catch { return null; }
}
/** "" = taken, else why not */
export function setAndroidSettings(s: Partial<AndroidSettings>): string {
  const b = bridge();
  if (!b) return "no Android bridge";
  return b.setSettings(JSON.stringify(s));
}
/** the control blocks changed: the service looks for the volume block again */
export function androidRefreshMaster() { bridge()?.refreshMaster(); }

/** "save as" in the app; false if cancelled */
export async function androidSave(data: Uint8Array, name: string, mime = "application/octet-stream"): Promise<boolean> {
  const b = bridge();
  if (!b) return false;
  const err = await call((id) => b.save(id, toB64(data), name, mime));
  if (err === "cancel") return false;
  if (err) throw new Error(err);
  return true;
}

/** self-update of the app (android/…/update/Updater.kt) */
export type UpdateState = "IDLE" | "CHECKING" | "CURRENT" | "AVAILABLE" | "DOWNLOADING" | "INSTALLING" | "PERMISSION" | "ERROR";
export interface AndroidUpdate { current: string; code: number; state: UpdateState; latest: string | null; progress: number; error: string }
export function androidUpdate(): AndroidUpdate | null {
  const b = bridge();
  if (!b?.update) return null;
  try { return JSON.parse(b.update()) as AndroidUpdate; } catch { return null; }
}
export const checkAndroidUpdate = () => bridge()?.checkUpdate?.();
export const installAndroidUpdate = () => bridge()?.installUpdate?.();
