// The bridge of the Android app (#29): window.ForgeAndroid (Kotlin) <-> transport of the GUI.
import { beforeAll, describe, expect, it } from "vitest";

const sent: string[] = [];
let bauds: number[] = [];
const g = globalThis as unknown as { window: Record<string, unknown>; btoa: typeof btoa; atob: typeof atob };
type Hooks = { data(b64: string): void; settle(id: number, err: string): void; attached(): void };
const hooks = () => g.window.__forgeAndroid as Hooks;
let android: typeof import("../src/lib/android");

beforeAll(async () => {
  g.window = {
    ForgeAndroid: {
      present: () => true,
      open: (id: number, baud: number) => { bauds.push(baud); setTimeout(() => hooks().settle(id, ""), 1); },
      write: (b64: string) => sent.push(atob(b64)),
      setBaud: (b: number) => bauds.push(b),
      close: () => {},
      save: (id: number, b64: string, name: string) => setTimeout(() => hooks().settle(id, name === "no.cfg" ? "cancel" : atob(b64) === "x" ? "" : "bad"), 1),
    },
  };
  android = await import("../src/lib/android");
});

describe("Android bridge", () => {
  it("is found", () => {
    expect(android.isAndroid()).toBe(true);
    expect(android.androidPresent()).toBe(true);
  });
  it("lines and raw bytes", async () => {
    const tr = await android.openAndroidSerial();
    expect(bauds).toEqual([38400]);
    const lines: string[] = [];
    tr.onLine((l) => lines.push(l));
    await tr.write("get.1 system.ping\r\n");
    expect(sent).toEqual(["get.1 system.ping\r\n"]);
    hooks().data(btoa("<.1 system.ping=\"po"));
    hooks().data(btoa("ng\"\r\n#.1 mute.out=[1]\r\n"));
    expect(lines).toEqual(['<.1 system.ping="pong"', "#.1 mute.out=[1]"]);
    tr.bytes!.flush();
    hooks().data(btoa("\x00\xff\x13"));
    expect([...(await tr.bytes!.read(10))]).toEqual([0, 255, 0x13]);
    await tr.setBaud!(460800);
    expect(bauds).toEqual([38400, 460800]);
  });
  it("save as", async () => {
    expect(await android.androidSave(new TextEncoder().encode("x"), "a.cfg")).toBe(true);
    expect(await android.androidSave(new TextEncoder().encode("x"), "no.cfg")).toBe(false);
    await expect(android.androidSave(new TextEncoder().encode("y"), "a.cfg")).rejects.toThrow("bad");
  });
  it("plugged in", () => {
    let n = 0;
    android.onAndroidAttached(() => n++);
    hooks().attached();
    expect(n).toBe(1);
  });
});
