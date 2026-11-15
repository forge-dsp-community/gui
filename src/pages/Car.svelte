<script lang="ts">
  // In the car (#29): the controls for driving on one screen of a 7" head unit (1024 x 600), large
  // touch targets: preset, master volume, mute per output and all, source, GALA and status. The
  // start page of the Android app; everything else stays on the other pages.
  import { onDestroy } from "svelte";
  import { t } from "../lib/i18n/index.svelte";
  import { app, loadPreset, muteAll, setMaster, toggleOut, watchSpeed, write } from "../lib/app.svelte";
  import { isPort, masterDb, masterOf, masterValue, sourceNames } from "../lib/device";
  import VFader from "../components/VFader.svelte";
  import { androidUpdate, isAndroid } from "../lib/android";

  const d = $derived(app.device);
  const ready = $derived(app.status === "online" && !app.bootloader);
  const presets = $derived(d.identity.presets || 4);
  const outs = $derived(d.mute.length || 8);
  const allMuted = $derived(d.mute.length > 0 && d.mute.every((m) => m));
  const sources = $derived(d.mixer.input !== null ? sourceNames(d) : []);
  const speed = $derived(d.speed && d.speed.source !== 255 ? d.speed : null);
  const supplyV = $derived(Array.isArray(d.supply) && typeof d.supply[1] === "number" && d.supply[1] > 0 ? d.supply[1] / 1000 : null);
  const errors = $derived(d.errors.reduce((n, e) => n + (Number(e[1]) || 0), 0));

  // master: the volume block (as the fader in the top bar), -60 dB .. 0 dB
  const MIN_DB = -60;
  const m = $derived(masterOf(d));
  const mv = $derived(m ? d.events[m.odd] : undefined);
  const mdb = $derived(mv === undefined ? null : masterDb(mv));
  const mtext = $derived(mdb === null ? "–" : mdb <= MIN_DB ? "−∞" : (mdb < 0 ? "−" : "") + Math.abs(mdb).toFixed(1));
  const put = (x: number) => setMaster(x <= MIN_DB ? 0 : masterValue(Math.min(0, x)));

  // a newer app in the releases: a hint to the settings
  let upd = $state(androidUpdate());
  const updTimer = setInterval(() => (upd = androidUpdate()), 5000);
  onDestroy(() => clearInterval(updTimer));

  let loading = $state<number | null>(null);
  let note = $state("");
  async function preset(n: number) {
    if (loading !== null || !ready) return;
    loading = n;
    note = "";
    try { note = await loadPreset(n); } finally { loading = null; }
  }
  function source(v: number) {
    d.mixer.input = v;
    write("mixer.in", v);
  }

  $effect(() => watchSpeed(isPort(d)));
  onDestroy(() => watchSpeed(false));
</script>

<div class="car" data-testid="car">
 <div class="col">
  <section class="panel presets" aria-label={t("car.presets")}>
    <h3>{t("car.presets")}</h3>
    <div class="row">
      {#each { length: presets } as _, n}
        <button class="big" class:on={d.preset.loaded === n} aria-pressed={d.preset.loaded === n} disabled={!ready || loading !== null}
          onclick={() => preset(n)}>{loading === n ? "…" : n + 1}</button>
      {/each}
    </div>
    {#if note}<p class="warn small">{note}</p>{/if}
  </section>

  <section class="panel mutes">
    <div class="head">
      <h3>{t("car.outputs")}</h3>
      <button class="big all" class:off={allMuted} disabled={!ready} onclick={muteAll}>{allMuted ? t("car.unmuteAll") : t("car.muteAll")}</button>
    </div>
    <div class="grid">
      {#each { length: outs } as _, i}
        <button class="big out" class:off={!!d.mute[i]} aria-pressed={!!d.mute[i]} disabled={!ready} onclick={() => toggleOut("mute", i + 1)}>
          <b>{i + 1}</b><span>{d.channelNames[i + 1] || t("car.outputN", { n: i + 1 })}</span>
        </button>
      {/each}
    </div>
  </section>

  {#if upd?.state === "AVAILABLE"}
    <button class="more upd" onclick={() => (app.page = "settings")}>{t("car.update", { v: upd.latest ?? "" })}</button>
  {/if}
  <!-- the app has no overview: the menu with all pages -->
  <button class="more" onclick={() => (isAndroid() ? (app.navOpen = true) : (app.page = "overview"))}>{t("car.allSettings")}</button>
 </div>
 <div class="col">
  <section class="panel master">
    <h3>{t("car.volume")}</h3>
    {#if m}
      <div class="mrow">
        <button class="big step" disabled={!ready || mdb === null} aria-label={t("car.quieter")} onclick={() => mdb !== null && put(Math.max(MIN_DB, mdb - 1))}>−</button>
        <div class="fader">
          <VFader horizontal frac={mdb === null ? 0 : (Math.max(MIN_DB, mdb) - MIN_DB) / -MIN_DB} label={t("ui.masterVolume")} valuetext="{mtext} dB"
            disabled={!ready || mdb === null} onfrac={(f) => put(Math.round((MIN_DB + f * -MIN_DB) * 2) / 2)} onstep={(s) => mdb !== null && put(Math.max(MIN_DB, mdb + s * 0.5))} />
        </div>
        <button class="big step" disabled={!ready || mdb === null} aria-label={t("car.louder")} onclick={() => mdb !== null && put(Math.min(0, mdb + 1))}>+</button>
        <span class="num val">{mtext} dB</span>
      </div>
    {:else}
      <p class="muted small">{t("car.noMaster")}</p>
    {/if}
  </section>

  {#if sources.length > 1}
    <section class="panel sources">
      <h3>{t("car.source")}</h3>
      <div class="row wrap">
        {#each sources as s}
          <button class="big" class:on={d.mixer.input === s.value} aria-pressed={d.mixer.input === s.value} disabled={!ready} onclick={() => source(s.value)}>{s.label}</button>
        {/each}
      </div>
    </section>
  {/if}

  <section class="panel status">
    {#if speed}
      <div class="kmh"><span class="num">{speed.kmh}</span> km/h
        <small>{t("car.galaBoost", { vol: (speed.vol / 10).toFixed(1).replace(".", ","), bass: (speed.bass / 10).toFixed(1).replace(".", ",") })}</small></div>
    {/if}
    <dl>
      <dt>{t("common.temperature")}</dt><dd class="num">{d.temperature !== null ? `${d.temperature.toFixed(1)} °C` : "–"}</dd>
      {#if supplyV !== null}<dt>{t("car.supply")}</dt><dd class="num">{supplyV.toFixed(1).replace(".", ",")} V</dd>{/if}
      <dt>{t("car.errors")}</dt><dd class="num" class:warn={errors > 0}>{errors}</dd>
    </dl>
  </section>
 </div>
</div>

<style>
  .car { display: grid; gap: .6rem; grid-template-columns: repeat(auto-fit, minmax(min(100%, 22rem), 1fr)); align-items: start; }
  .col { display: grid; gap: .6rem; min-width: 0; }
  .panel { padding: .7rem .8rem; display: grid; gap: .5rem; }
  h3 { margin: 0; font-size: .9rem; color: var(--muted); font-weight: 600; }
  .row { display: flex; gap: .5rem; }
  .row.wrap { flex-wrap: wrap; }
  .sources .big { font-size: 1rem; flex: 1 1 6.5rem; padding: .3rem .5rem; }
  .big { min-height: 3.4rem; min-width: 3.4rem; font-size: 1.25rem; border-radius: 12px; flex: 1; }
  .big.on { background: color-mix(in srgb, var(--accent) 25%, transparent); border-color: var(--accent); color: var(--accent); font-weight: 600; }
  .mrow { display: flex; align-items: center; gap: .6rem; }
  .step { flex: none; width: 3.4rem; font-size: 1.6rem; }
  .fader { flex: 1; min-width: 0; }
  .fader :global(.vf.h) { width: 100%; height: 3rem; }
  .val { min-width: 5.5rem; text-align: right; font-size: 1.15rem; }
  .head { display: flex; justify-content: space-between; align-items: center; gap: .5rem; }
  .all { flex: none; padding: 0 1rem; font-size: 1rem; }
  .grid { display: grid; gap: .5rem; grid-template-columns: repeat(4, 1fr); }
  .out { display: grid; gap: .1rem; justify-items: center; padding: .3rem; font-size: .85rem; min-width: 0; }
  .out b { font-size: 1.2rem; }
  .out span { max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--muted); }
  .off { background: color-mix(in srgb, var(--err) 22%, transparent); border-color: var(--err); color: var(--err); }
  .out.off span { color: var(--err); }
  .kmh { font-size: 1rem; color: var(--muted); }
  .kmh .num { font-size: 2rem; font-weight: 600; color: var(--fg); }
  .kmh small { display: block; font-size: .85rem; }
  dl { display: grid; grid-template-columns: auto 1fr; gap: .2rem .8rem; margin: 0; font-size: .95rem; }
  dt { color: var(--muted); }
  dd { margin: 0; }
  .warn { color: var(--warn); }
  .small { font-size: .82rem; margin: 0; }
  .more { min-height: 3rem; }
  .upd { border-color: var(--accent); color: var(--accent); }
</style>
