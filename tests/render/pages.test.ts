import { beforeAll, describe, expect, it } from "vitest";
import { flushSync, mount, unmount } from "svelte";
import type { Component } from "svelte";
import { app, connect, setDigitalOnly, type PageId } from "../../src/lib/app.svelte";
import Overview from "../../src/pages/Overview.svelte";
import Mixer from "../../src/pages/Mixer.svelte";
import Filters from "../../src/pages/Filters.svelte";
import Delay from "../../src/pages/Delay.svelte";
import Modc from "../../src/pages/Modc.svelte";
import Presets from "../../src/pages/Presets.svelte";
import Biquads from "../../src/pages/Biquads.svelte";
import Diagnose from "../../src/pages/Diagnose.svelte";
import SdCard from "../../src/pages/SdCard.svelte";
import Firmware from "../../src/pages/Firmware.svelte";
import Settings from "../../src/pages/Settings.svelte";
import Console from "../../src/pages/Console.svelte";

// Every page builds in the demo without throwing: catches errors the unit tests do not see
// (e.g. a derived value read before the first channel is chosen).
const PAGES: [PageId, Component, string?][] = [
  ["overview", Overview], ["mixer", Mixer], ["filters", Filters], ["filters", Filters, "?demo&over=3,5"], ["delay", Delay],
  ["modc", Modc], ["presets", Presets], ["biquads", Biquads], ["diagnose", Diagnose], ["sdcard", SdCard],
  ["firmware", Firmware], ["settings", Settings], ["console", Console],
];

describe("pages build in the demo", () => {
  beforeAll(async () => {
    await connect("demo");
  });
  for (const [id, Page, search] of PAGES) {
    it(`${id}${search ?? ""}`, () => {
      history.replaceState(null, "", `/${search ?? "?demo"}`);
      app.page = id;
      const el = document.createElement("div");
      document.body.appendChild(el);
      const c = mount(Page, { target: el });
      flushSync();
      expect(el.querySelector("h2")?.textContent?.length).toBeGreaterThan(0);
      if (search?.includes("over=")) expect(el.querySelectorAll("path.sum").length).toBe(1);   // crossover sum drawn
      unmount(c);
      el.remove();
    });
  }
});

// forge-dsp-firmware#82: S/PDIF from the DSP carries the mixer outputs 1/2 of core 1, before the output
// chains; the view "digital only" shows those two in the mixer, the sums A/B in Filters & EQ and no
// output delay
describe("S/PDIF from the DSP, view digital only", () => {
  beforeAll(async () => {
    await connect("demo");
  });
  const render = (Page: Component) => {
    const el = document.createElement("div");
    document.body.appendChild(el);
    const c = mount(Page, { target: el });
    flushSync();
    return { el, done: () => { unmount(c); el.remove(); } };
  };
  it("mixer: only outputs 1/2, named S/PDIF L/R", () => {
    history.replaceState(null, "", "/?demo");
    app.device.spdifOut = 1;
    setDigitalOnly(true);
    const own = app.device.channelNames;
    app.device.channelNames = {};    // the demo names its outputs; own names take precedence
    const { el, done } = render(Mixer);
    const outs = [...el.querySelectorAll(".rlabel")].filter((r) => r.querySelector(".num"));
    expect(outs.map((r) => r.querySelector(".num")?.textContent)).toEqual(["1", "2"]);
    expect(outs.map((r) => r.querySelector(".rn")?.textContent)).toEqual(["S/PDIF L", "S/PDIF R"]);
    done();
    app.device.channelNames = own;
  });
  it("filters: only the sums A/B", () => {
    const { el, done } = render(Filters);
    const tabs = el.querySelectorAll(".chans button");
    expect(tabs.length).toBe(2);
    expect([...tabs].every((b) => b.classList.contains("bus"))).toBe(true);
    done();
  });
  it("delay: no output strip, a note instead", () => {
    const { el, done } = render(Delay);
    expect(el.querySelector('[data-testid="delay-spdif-note"]')).not.toBeNull();
    done();
  });
  it("without the view: all outputs back, filters list outputs and sums", () => {
    setDigitalOnly(false);
    const { el, done } = render(Filters);
    const tabs = [...el.querySelectorAll(".chans button")];
    expect(tabs.filter((b) => !b.classList.contains("bus")).length).toBe(app.device.gain.out.length);
    expect(tabs.filter((b) => b.classList.contains("bus")).length).toBe(2);
    done();
    app.device.spdifOut = 0;
  });
});

describe("S/PDIF from the DSP, hints (gui#42)", () => {
  beforeAll(async () => {
    await connect("demo");
  });
  const show = (Page: Component) => {
    const el = document.createElement("div");
    document.body.appendChild(el);
    const c = mount(Page, { target: el });
    flushSync();
    return { el, done: () => { unmount(c); el.remove(); } };
  };
  it("mixer, filters and delay say what acts only on the analog outputs", () => {
    history.replaceState(null, "", "/?demo");
    setDigitalOnly(false);
    app.device.spdifOut = 1;
    for (const [Page, id] of [[Mixer, "mixer-spdif-hint"], [Filters, "filters-spdif-hint"], [Delay, "delay-spdif-hint"]] as const) {
      const { el, done } = show(Page);
      expect(el.querySelector(`[data-testid="${id}"]`), id).not.toBeNull();
      done();
    }
    app.device.spdifOut = 0;
    const { el, done } = show(Mixer);
    expect(el.querySelector('[data-testid="mixer-spdif-hint"]')).toBeNull();
    done();
  });
});

describe("filters: passes of higher order as one block (gui#47)", () => {
  beforeAll(async () => {
    await connect("demo");
  });
  it("output 1: the two Butterworth 12 at 3200 Hz are one Linkwitz-Riley 24 card with 2 biquads", () => {
    history.replaceState(null, "", "/?demo");
    const el = document.createElement("div");
    document.body.appendChild(el);
    const c = mount(Filters, { target: el });
    flushSync();
    const cards = [...el.querySelectorAll(".cards .card")];
    const blk = cards.find((k) => k.querySelector('[data-testid="block-biquads"]'))!;
    expect(blk.querySelector('[data-testid="block-biquads"]')?.textContent).toMatch(/^2 /);
    expect((blk.querySelector(".cardhead select") as HTMLSelectElement).value).toBe("lr:hp");
    const slope = blk.querySelector(".knob .val select") as HTMLSelectElement;
    expect([...slope.options].map((o) => o.textContent)).toEqual(["12", "24", "36", "48"]);
    expect(slope.selectedOptions[0].textContent).toBe("24");
    expect(cards.length).toBe(2);   // block + treble shelf; the second high pass has no card of its own
    unmount(c);
    el.remove();
  });
});
