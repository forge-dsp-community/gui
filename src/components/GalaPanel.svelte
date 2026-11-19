<script lang="ts">
  // GALA (forge-dsp-firmware#71) inside its control block: the curve of the block's fields, what
  // the device applies now (system.kmh, read once a second while shown) and a test speed for the
  // command source (sent every second, the firmware falls back to 0 km/h after its timeout).
  import { onDestroy } from "svelte";
  import { t } from "../lib/i18n/index.svelte";
  import { app, watchSpeed, writeOnce } from "../lib/app.svelte";
  import { galaBoost, galaFullAt, galaParams, GALA_NONE, type Modc } from "../lib/device";

  let { m }: { m: Modc } = $props();
  const p = $derived(galaParams(m));
  const live = $derived(app.device.speed);
  const dec = (n: number) => String(+n.toFixed(1)).replace(".", ",");

  // curve: 0..KMH km/h, 0..top dB
  const KMH = 200, W = 320, H = 120, L = 30, B = 18;
  const top = $derived(Math.max(1, p.outs || p.sums ? p.volMax : 0, p.bassSlope ? p.bassMax : 0));
  const x = (k: number) => L + (k / KMH) * (W - L - 6);
  const y = (db: number) => H - B - (db / top) * (H - B - 6);
  const path = (f: (k: number) => number) => Array.from({ length: KMH / 2 + 1 }, (_, i) => `${i ? "L" : "M"}${x(i * 2).toFixed(1)},${y(f(i * 2)).toFixed(1)}`).join("");
  const volPath = $derived(p.outs || p.sums ? path((k) => galaBoost(p, k).vol) : "");
  const bassPath = $derived(p.bassSlope ? path((k) => galaBoost(p, k).bass) : "");
  const full = $derived(galaFullAt(p));

  watchSpeed(true);
  let testOn = $state(false);
  let testKmh = $state(80);
  let testErr = $state("");
  const timer = setInterval(async () => {
    if (!testOn || app.status !== "online") return;
    testErr = await writeOnce("system.kmh", testKmh);
    if (testErr) testOn = false;
  }, 1000);
  onDestroy(() => { watchSpeed(false); clearInterval(timer); });
</script>

<div class="gala" data-testid="gala">
  <div class="cols">
    <div class="fig">
      <svg viewBox="0 0 {W} {H}" role="img" aria-label={t("gala.curve")}>
        {#each [0, 50, 100, 150, 200] as k}
          <line class="grid" x1={x(k)} x2={x(k)} y1={6} y2={H - B} /><text class="ax" x={x(k)} y={H - 4} text-anchor="middle">{k}</text>
        {/each}
        {#each [0, top / 2, top] as db}
          <line class="grid" x1={L} x2={W - 6} y1={y(db)} y2={y(db)} /><text class="ax" x={L - 4} y={y(db) + 3} text-anchor="end">{dec(db)}</text>
        {/each}
        {#if volPath}<path class="vol" d={volPath} />{/if}
        {#if bassPath}<path class="bass" d={bassPath} />{/if}
        {#if live && live.source !== GALA_NONE && live.kmh <= KMH}<line class="now" x1={x(live.kmh)} x2={x(live.kmh)} y1={6} y2={H - B} />{/if}
      </svg>
      <div class="legend">
        {#if volPath}<span><i class="vol"></i>{t("gala.volume")}</span>{/if}
        {#if bassPath}<span><i class="bass"></i>{t("gala.bass")}</span>{/if}
        <span class="muted">km/h → dB</span>
      </div>
      <p class="muted small">
        {#if !p.outs && !p.sums && !p.bassSlope}{t("gala.nothing")}
        {:else}{t("gala.summary", { start: p.start, slope: dec(p.volSlope), max: dec(p.volMax) })}{#if full !== null && (p.outs || p.sums)}{" " + t("gala.fullAt", { kmh: full })}{/if}{/if}
      </p>
      {#if p.source > 0}<p class="muted small">{t("gala.pulseHint")}</p>{/if}
    </div>

    <div class="now-box" aria-live="polite">
      <h4>{t("gala.now")}</h4>
      {#if !live}
        <p class="muted small">{t("gala.reading")}</p>
      {:else if live.source === GALA_NONE}
        <p class="muted small">{t("gala.off")}</p>
      {:else}
        <div class="big num">{live.kmh} <small>km/h</small></div>
        <div class="vals"><span>{t("gala.volume")} <b class="num">+{dec(live.vol / 10)} dB</b></span>
          <span>{t("gala.bass")} <b class="num">+{dec(live.bass / 10)} dB</b></span></div>
      {/if}
      {#if p.source === 0}
        <h4>{t("gala.test")}</h4>
        <div class="test">
          <input class="num short" type="number" min="0" max="300" bind:value={testKmh} aria-label={t("gala.testKmh")} /><i>km/h</i>
          <button class:on={testOn} aria-pressed={testOn} onclick={() => { testOn = !testOn; testErr = ""; }}>{testOn ? t("gala.testStop") : t("gala.testStart")}</button>
        </div>
        <p class="muted small">{t("gala.testHint")}</p>
        {#if testErr}<p class="warn small">{testErr}</p>{/if}
      {/if}
    </div>
  </div>
</div>

<style>
  .gala { margin-top: .4rem; }
  .cols { display: grid; gap: 1rem; grid-template-columns: repeat(auto-fit, minmax(min(100%, 18rem), 1fr)); align-items: start; }
  .fig { display: grid; gap: .3rem; }
  svg { width: 100%; max-width: 30rem; height: auto; }
  .grid { stroke: var(--line); stroke-width: 1; }
  .ax { font-size: 9px; fill: var(--faint); }
  path { fill: none; stroke-width: 2; }
  path.vol, i.vol { stroke: var(--accent); background: var(--accent); }
  path.bass, i.bass { stroke: var(--warn); background: var(--warn); }
  .now { stroke: var(--ok); stroke-width: 1.5; stroke-dasharray: 3 3; }
  .legend { display: flex; flex-wrap: wrap; gap: .3rem 1rem; font-size: .8rem; color: var(--muted); }
  .legend span { display: inline-flex; align-items: center; gap: .35rem; }
  .legend i { width: .9rem; height: .2rem; border-radius: 2px; display: inline-block; }
  .small { font-size: .82rem; margin: 0; }
  .warn { color: var(--warn); }
  .now-box { display: grid; gap: .4rem; align-content: start; }
  h4 { margin: .2rem 0 0; font-size: .85rem; color: var(--muted); font-weight: 600; }
  .big { font-size: 1.8rem; font-weight: 600; }
  .big small { font-size: .9rem; color: var(--muted); font-weight: 400; }
  .vals { display: flex; flex-wrap: wrap; gap: .3rem 1.2rem; font-size: .9rem; color: var(--muted); }
  .vals b { color: var(--fg); font-weight: 600; }
  .test { display: flex; align-items: center; gap: .5rem; flex-wrap: wrap; }
  .test i { font-style: normal; color: var(--muted); }
  .short { width: 5.5rem; }
  button.on { background: color-mix(in srgb, var(--ok) 20%, transparent); border-color: var(--ok); color: var(--ok); }
</style>
