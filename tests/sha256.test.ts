import { describe, expect, it } from "vitest";
import { createHash } from "node:crypto";
import { sha256Hex, sha256Sync } from "../src/lib/util/sha256";

const ref = (b: Uint8Array) => createHash("sha256").update(b).digest("hex");
const hex = (b: Uint8Array) => [...b].map((x) => x.toString(16).padStart(2, "0")).join("");

describe("sha256 fallback (#36, ESP32 bridge without crypto.subtle)", () => {
  it("known vectors", () => {
    expect(hex(sha256Sync(new Uint8Array()))).toBe("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
    expect(hex(sha256Sync(new TextEncoder().encode("abc")))).toBe("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
  });
  it("matches node:crypto at every block boundary and for a firmware-sized image", () => {
    for (const n of [1, 55, 56, 63, 64, 65, 119, 120, 127, 128, 1000, 128 * 1024]) {
      const b = Uint8Array.from({ length: n }, (_, i) => (i * 131 + 7) & 0xff);
      expect(hex(sha256Sync(b)), `length ${n}`).toBe(ref(b));
    }
  });
  it("sha256Hex without crypto.subtle uses the fallback", async () => {
    const b = new TextEncoder().encode("forge");
    const orig = Object.getOwnPropertyDescriptor(globalThis, "crypto");
    Object.defineProperty(globalThis, "crypto", { value: {}, configurable: true });
    try {
      expect(await sha256Hex(b)).toBe(ref(b));
    } finally {
      if (orig) Object.defineProperty(globalThis, "crypto", orig);
    }
    expect(await sha256Hex(b)).toBe(ref(b));
  });
});
