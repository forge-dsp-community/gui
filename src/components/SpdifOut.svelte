<script lang="ts">
  // S/PDIF output behind the DSP (#42, forge-dsp-firmware#82): the second CT7302 sends DSP outputs 7/8
  // (core 3, after mixer, filters, delay, gain) at the optical port 2 instead of scanning the inputs.
  import { i18n, t } from "../lib/i18n/index.svelte";
  import { app, setDigitalOnly, view, writeOnce } from "../lib/app.svelte";

  const d = $derived(app.device);
  // a port firmware before 2.7 does not know system.spdif.out: section shown, choice locked
  const known = $derived(d.spdifOut !== null);
  let err = $state("");
  let busy = $state(false);

  // #44 (forge-dsp-firmware#83): kHz values of the firmware, 44/88/176 = 44.1/88.2/176.4
  const RATES: [number, number][] = [[0, 0], [44, 44.1], [48, 48], [96, 96], [192, 192], [32, 32], [88, 88.2], [176, 176.4]];
  const khz = (x: number) => x.toLocaleString(i18n.lang === "de" ? "de-DE" : "en-US");
  let rateErr = $state("");
  async function setRate(v: number) {
    if (busy || d.spdifRate === v) return;
    busy = true;
    rateErr = await writeOnce("system.spdif.rate", v);
    if (!rateErr) d.spdifRate = v;
    busy = false;
  }

  async function set(v: number) {
    if (busy || !known || d.spdifOut === v) return;
    busy = true;
    err = await writeOnce("system.spdif.out", v);
    if (!err) d.spdifOut = v;
    else if (v === 0) setDigitalOnly(false);
    busy = false;
  }
</script>

<section class="panel box" data-testid="spdif-out">
  <h3>{t("spdif.title")} <span class="beta">beta</span></h3>
  <p class="muted small">{t("spdif.intro")}</p>
  {#if !known}<p class="small warntxt" data-testid="spdif-needs-fw">{t("spdif.needsFw")}</p>{/if}
  <div class="choice" role="radiogroup" aria-label={t("spdif.title")}>
    <label><input type="radio" name="spdifout" checked={d.spdifOut !== 1} disabled={busy || !known} onchange={() => set(0)} />
      <span><strong>{t("spdif.scan")}</strong><small class="muted">{t("spdif.scanHint")}</small></span></label>
    <label><input type="radio" name="spdifout" checked={d.spdifOut === 1} disabled={busy || !known} onchange={() => set(1)} />
      <span><strong>{t("spdif.dsp")}</strong><small class="muted">{t("spdif.dspHint")}</small></span></label>
  </div>
  {#if err}<p class="small errtxt">{t("spdif.refused", { err })}</p>{/if}
  {#if d.spdifOut === 1}
    <ul class="small notes">
      <li>{t("spdif.hwJumper")}</li>
      <li>{t("spdif.outs")}</li>
      <li>{t("spdif.mute")}</li>
      <li>{t("spdif.cores")}</li>
    </ul>
    {#if d.spdifRate !== null}
      <label class="row">
        <span><strong>{t("spdif.rate")}</strong><small class="muted">{t("spdif.rateHint")}</small></span>
        <select data-testid="spdif-rate" value={d.spdifRate} disabled={busy} onchange={(e) => setRate(Number(e.currentTarget.value))}>
          {#each RATES as [v, label] (v)}<option value={v}>{v === 0 ? t("spdif.rateRef") : `${khz(label)} kHz`}</option>{/each}
        </select>
      </label>
      {#if rateErr}<p class="small errtxt">{t("spdif.refused", { err: rateErr })}</p>{/if}
    {/if}
    <label class="row">
      <span><strong>{t("spdif.digitalOnly")}</strong><small class="muted">{t("spdif.digitalOnlyHint")}</small></span>
      <input type="checkbox" class="toggle" checked={view.digitalOnly} onchange={(e) => setDigitalOnly(e.currentTarget.checked)} />
    </label>
    <div><button onclick={() => (app.page = "biquads")}>{t("spdif.toPool")}</button></div>
  {/if}
</section>

<style>
  .box { padding: .9rem 1rem; display: grid; gap: .6rem; margin-bottom: .75rem; }
  .small { font-size: .85rem; margin: 0; }
  .beta { font-size: .7rem; padding: .05rem .4rem; border-radius: 999px; border: 1px solid var(--warn); color: var(--warn); vertical-align: middle; }
  .choice { display: grid; gap: .45rem; }
  .choice label { display: flex; gap: .55rem; align-items: flex-start; }
  .choice span, .row span { display: grid; gap: .1rem; }
  .notes { margin: 0; padding-left: 1.1rem; display: grid; gap: .2rem; }
  .row { display: flex; justify-content: space-between; align-items: center; gap: 1rem; }
  .toggle { width: 1.2rem; height: 1.2rem; accent-color: var(--accent); flex: none; }
  .errtxt { color: var(--err); }
  .warntxt { color: var(--warn); }
</style>
