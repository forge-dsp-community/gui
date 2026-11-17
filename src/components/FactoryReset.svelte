<script lang="ts">
  // Factory reset (#40, forge-dsp-firmware#81): deletes all settings of the Forge; the firmware keeps the
  // calibrations (main clock fine tune, supply calibration, VCC calibration) and the identity. As a
  // second safety net the calibrations are shown and offered as a file before, and compared after.
  import { t } from "../lib/i18n/index.svelte";
  import { app, query, recoverRate, reload, writeOnce } from "../lib/app.svelte";
  import { exportState } from "../lib/device";
  import { saveFile } from "../lib/saveFile";
  import { androidKeepScreenOn } from "../lib/android";

  type Cal = { fine: number | null; supply: number[] | null; vcc: number | null };
  let phase = $state<"idle" | "confirm" | "running" | "done">("idle");
  let before = $state<Cal | null>(null);
  let after = $state<Cal | null>(null);
  let err = $state("");
  let waited = $state(0);
  let restored = $state("");

  const MAINCLOCK = 68;   // MODC type "main clock": sets 1/2 = fine tune (int16)
  const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
  const int16 = (v: number) => (v > 32767 ? v - 65536 : v);
  const day = () => new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
  const model = () => app.device.identity.model || "forge";

  function fineTune(): number | null {
    const b = Object.values(app.device.modc).find((m) => m.type === MAINCLOCK && m.enable);
    return b ? int16((b.sets[0] ?? 0) | ((b.sets[1] ?? 0) << 8)) : null;
  }
  async function readCal(): Promise<Cal> {
    const one = async (reg: string) => (await query(reg)).find((a) => a.kind === "get" && a.register === reg)?.value ?? null;
    const s = await one("system.supply.cal"), v = await one("system.supply.vcc");
    return {
      fine: fineTune(),
      supply: Array.isArray(s) ? s.map(Number) : null,
      vcc: typeof v === "number" ? v : v === null ? null : Number(v),
    };
  }
  const calText = (c: Cal | null) => !c ? "–" : [
    `mainclock.fine = ${c.fine ?? t("factory.notSet")}`,
    `system.supply.cal = ${c.supply && !c.supply.every((x) => x === -1) ? `[${c.supply.join(",")}]` : t("factory.notSet")}`,
    `system.supply.vcc = ${c.vcc ?? "–"}${c.vcc === 10000 ? ` (${t("factory.uncalibrated")})` : ""}`,
  ].join("\n");
  const same = (a: Cal | null, b: Cal | null) => !!a && !!b && JSON.stringify(a.supply) === JSON.stringify(b.supply) && a.vcc === b.vcc;

  async function open() {
    err = ""; restored = "";
    before = await readCal();
    phase = "confirm";
  }
  function saveBackup() {
    saveFile(exportState(app.device), `forge-zustand-${model()}-${day()}.json`, "application/json");
    saveFile(`Forge ${model()} - Kalibrierung vor dem Werks-Reset (${day()})\n${calText(before)}\n`, `forge-kalibrierung-${model()}-${day()}.txt`, "text/plain");
  }
  async function run() {
    if (!confirm(t("factory.confirmFinal"))) return;
    phase = "running"; err = ""; waited = 0;
    app.exclusive = true;
    androidKeepScreenOn(true);
    try {
      // a slow line answers late (seen in the Android VM after 1.5 s): wait longer; without an answer
      // the reset may run all the same, so only an error answer (older firmware) stops here
      const r = await writeOnce("system.reinit", 1, 8000);
      if (r && !/timeout|answer|antwort/i.test(r)) { err = r; phase = "confirm"; return; }
      // the Forge resets, erases and resets again (about 90 s); it answers again when through, at
      // 38400 Bd after its restart: the port is not exclusive meanwhile and the rate is checked each
      // round (the Android service may have switched to the fast rate between the two restarts)
      app.exclusive = false;
      const t0 = Date.now();
      let up = false;
      while (!up && Date.now() - t0 < 300000) {
        await sleep(3000);
        waited = Math.round((Date.now() - t0) / 1000);
        if (Date.now() - t0 < 20000) continue;
        await recoverRate();
        up = (await query("system.ping")).some((a) => a.kind === "get");
        // the factory reset restarts the Forge twice: the first answers can come from the start in
        // between; it counts only when it still answers 20 s later
        if (up) {
          await sleep(20000);
          await recoverRate();
          up = (await query("system.ping")).some((a) => a.kind === "get");
        }
      }
      if (!up) { err = t("factory.noAnswer"); phase = "confirm"; return; }
      app.exclusive = false;
      await reload();
      if (app.device.identity.port === null) await reload();   // a restart came in between: once more
      after = await readCal();
      phase = "done";
    } finally {
      app.exclusive = false;
      androidKeepScreenOn(false);
    }
  }
  async function restoreCal() {
    if (!before?.supply || before.supply.every((x) => x === -1)) return;
    const r = await writeOnce("system.supply.cal", before.supply);
    restored = r || t("factory.restored");
    if (!r) after = await readCal();
  }
</script>

<section class="panel box danger">
  <h3>{t("factory.title")}</h3>
  <p class="muted small">{t("factory.intro")}</p>
  {#if phase === "idle"}
    <div><button class="bad" onclick={open} disabled={app.status !== "online"}>{t("factory.start")}</button></div>
  {:else if phase === "confirm"}
    <p class="small">{t("factory.deletes")}</p>
    <p class="small"><strong>{t("factory.keeps")}</strong></p>
    <pre class="cal">{calText(before)}</pre>
    <p class="small">{t("factory.noteDown")}</p>
    {#if err}<p class="small errtxt">{err}</p>{/if}
    <div class="btns">
      <button onclick={saveBackup}>{t("factory.saveBackup")}</button>
      <button class="bad" onclick={run}>{t("factory.run")}</button>
      <button onclick={() => (phase = "idle")}>{t("factory.cancel")}</button>
    </div>
  {:else if phase === "running"}
    <p class="small"><strong>{t("factory.running", { s: waited })}</strong></p>
    <progress max="90" value={Math.min(waited, 90)}></progress>
  {:else}
    <p class="small okt">{t("factory.done")}</p>
    <pre class="cal">{calText(after)}</pre>
    {#if same(before, after)}
      <p class="small okt">{t("factory.calKept")}</p>
    {:else}
      <p class="small errtxt">{t("factory.calChanged")}</p>
      <pre class="cal">{calText(before)}</pre>
      {#if before?.supply && !before.supply.every((x) => x === -1)}
        <div><button onclick={restoreCal}>{t("factory.restoreSupply")}</button></div>
      {/if}
      {#if restored}<p class="small">{restored}</p>{/if}
    {/if}
    <div><button onclick={() => (phase = "idle")}>{t("factory.close")}</button></div>
  {/if}
</section>

<style>
  .box { padding: .9rem 1rem; display: grid; gap: .6rem; margin-bottom: .75rem; }
  .danger { border-color: color-mix(in srgb, var(--err) 45%, var(--line)); }
  .small { font-size: .85rem; margin: 0; }
  .cal { margin: 0; padding: .6rem .8rem; background: var(--raised); border-radius: 6px; font-size: 1rem; white-space: pre-wrap; }
  .btns { display: flex; gap: .5rem; flex-wrap: wrap; }
  .bad { border-color: var(--err); color: var(--err); }
  .errtxt { color: var(--err); }
  .okt { color: var(--ok); }
  progress { width: 100%; }
</style>
