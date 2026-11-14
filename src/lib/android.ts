// The Android app (android/, #29): the same GUI in a WebView. Chrome on Android has no Web Serial for
// USB-serial adapters, so the app lends its USB host access through a JavaScript interface:
//   window.ForgeAndroid  (Kotlin, android/.../ui/Bridge.kt)
//     present(): boolean                  a Forge (CP2102) is plugged in
//     open(id, baud)                      permission + open; answer through settle(id, error)
//     write(base64) / setBaud(baud) / close()
//     save(id, base64, name, mime)        "save as" (Storage Access Framework); settle(id, error|"cancel")
//   window.__forgeAndroid  (this file)
//     data(base64)                        received bytes
//     gone()                              the Forge was unplugged
//     settle(id, error)                   end of an asynchronous call ("" = ok)
import { ByteQueue, LineSplitter, type Transport } from "./transport/types";

interface Bridge {
  present(): boolean;
  open(id: number, baud: number): void;
  write(b64: string): void;
  setBaud(baud: number): void;
  close(): void;
  save(id: number, b64: string, name: string, mime: string): void;
}

const bridge = (): Bridge | null => (typeof window !== "undefined" ? ((window as unknown as { ForgeAndroid?: Bridge }).ForgeAndroid ?? null) : null);

/** running inside the Android app */
export const isAndroid = () => bridge() !== null;

const waiting = new Map<number, (err: string) => void>();
let nextId = 1;
let onData: ((b: Uint8Array) => void) | null = null;
let attached: (() => void) | null = null;
/** the Forge was plugged in (USB_DEVICE_ATTACHED) */
export function onAndroidAttached(fn: () => void) { attached = fn; }

const toB64 = (b: Uint8Array) => { let s = ""; for (let i = 0; i < b.length; i += 0x8000) s += String.fromCharCode(...b.subarray(i, i + 0x8000)); return btoa(s); };
const fromB64 = (s: string) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));

if (typeof window !== "undefined")
  (window as unknown as { __forgeAndroid: unknown }).__forgeAndroid = {
    data: (b64: string) => onData?.(fromB64(b64)),
    attached: () => attached?.(),
    gone: () => {},   // the requests then go unanswered; the app finds the rate/port again (as with Web Serial)
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
    bytes: { write: async (d) => b.write(toB64(d)), read: (ms) => queue.read(ms), flush: () => queue.flush() },
    async setBaud(r) { queue.flush(); b.setBaud(r); },
    async close() { onData = null; b.close(); },
  };
}

/** "save as" in the app; false if cancelled */
export async function androidSave(data: Uint8Array, name: string, mime = "application/octet-stream"): Promise<boolean> {
  const b = bridge();
  if (!b) return false;
  const err = await call((id) => b.save(id, toB64(data), name, mime));
  if (err === "cancel") return false;
  if (err) throw new Error(err);
  return true;
}
