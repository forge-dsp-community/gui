import { LineSplitter, type Transport } from "./types";

/**
 * ESP32 bridge: each WebSocket text frame is passed to the RS485 port as is, every line the
 * Forge sends comes back as a text frame. Default URL: the host that served the page.
 */
export function defaultBridgeUrl(): string {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}/ws`;
}

export function openWebSocket(url = defaultBridgeUrl()): Promise<Transport> {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(url);
    const listeners: ((l: string) => void)[] = [];
    const closed: ((why: string) => void)[] = [];
    let closing = false, opened = false;
    // #34: the bridge restarted or the Wi-Fi went away; before the open it is a failed connect
    ws.onclose = (e) => { if (opened && !closing) closed.forEach((f) => f(`WebSocket closed (${e.code})`)); };
    const split = new LineSplitter((l) => listeners.forEach((f) => f(l)));
    ws.onmessage = (e) => split.push(typeof e.data === "string" ? e.data : "");
    ws.onerror = () => reject(new Error(`WebSocket ${url} failed`));
    ws.onopen = () => {
      opened = true;
      resolve({
        name: "ESP32",
        write: async (line) => {
          if (ws.readyState !== WebSocket.OPEN) throw new Error("WebSocket closed");
          ws.send(line);
        },
        onLine: (fn) => listeners.push(fn),
        onClose: (fn) => closed.push(fn),
        close: async () => { closing = true; ws.close(); },
      });
    };
  });
}

/** True when the page was served by the ESP32 (it answers /api/info). */
export async function servedByBridge(): Promise<boolean> {
  try {
    const r = await fetch("/api/info", { cache: "no-store" });
    return r.ok && (await r.json()).bridge === "forge-dsp";
  } catch {
    return false;
  }
}
