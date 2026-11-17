import { describe, expect, it } from "vitest";
import { openWebSerial } from "../src/lib/transport/webserial";

/** a port like Chromium's: new streams on every open, the writer of a closed open is released */
function fakePort() {
  const written: string[] = [];
  const port = {
    readable: null as ReadableStream<Uint8Array> | null,
    writable: null as WritableStream<Uint8Array> | null,
    async open() {
      await new Promise((r) => setTimeout(r, 5));
      this.readable = new ReadableStream<Uint8Array>({ pull: () => new Promise(() => undefined) });
      this.writable = new WritableStream<Uint8Array>({ write: (c) => { written.push(new TextDecoder().decode(c)); } });
    },
    async close() { await new Promise((r) => setTimeout(r, 5)); },
  };
  return { port, written };
}

describe("Web Serial transport (released writer)", () => {
  it("a write during a rate change waits for the reopened port", async () => {
    const { port, written } = fakePort();
    const tr = await openWebSerial(38400, port);
    const change = tr.setBaud!(460800);
    const w = tr.write("get.1 system.ping\r\n");   // in the gap between close and reopen
    await change;
    await w;
    expect(written).toEqual(["get.1 system.ping\r\n"]);
  });
  it("a write after close fails with a plain message", async () => {
    const { port } = fakePort();
    const tr = await openWebSerial(38400, port);
    await tr.close();
    await expect(tr.write("x\r\n")).rejects.toThrow("USB port closed");
  });
});
