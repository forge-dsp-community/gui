<script lang="ts">
  // Settings of the background service of the Android app (#29): how the Android volume acts on
  // the Forge, start with the head unit, state. Only in the app (window.ForgeAndroid).
  import { onDestroy } from "svelte";
  import { t, tk, tx } from "../lib/i18n/index.svelte";
  import { androidSettings, androidUpdate, checkAndroidUpdate, installAndroidUpdate, setAndroidSettings,
    type AndroidSettings, type VolumeMode } from "../lib/android";
  import { masterDb } from "../lib/device";

  let s = $state<AndroidSettings | null>(androidSettings());
  let err = $state("");
  let u = $state(androidUpdate());
  const busy = $derived(u?.state === "CHECKING" || u?.state === "DOWNLOADING" || u?.state === "INSTALLING");
  let tick = 0;
  const timer = setInterval(() => { u = androidUpdate(); if (++tick % 4 === 0) s = androidSettings(); }, 500);
  onDestroy(() => clearInterval(timer));

  const MODES: { v: VolumeMode; t: string; d: string }[] = [
    { v: "OFF", t: tk("android.volOff"), d: tk("android.volOffHint") },
    { v: "MIRROR", t: tk("android.volMirror"), d: tk("android.volMirrorHint") },
    { v: "MIRROR_FIXED", t: tk("android.volFixed"), d: tk("android.volFixedHint") },
    { v: "REMOTE", t: tk("android.volRemote"), d: tk("android.volRemoteHint") },
    { v: "ABSOLUTE", t: tk("android.volAbsolute"), d: tk("android.volAbsoluteHint") },
  ];
  const usable = (m: VolumeMode) => m !== "ABSOLUTE" || !!s?.absoluteAvailable || s?.volumeMode === "ABSOLUTE";
  function put(p: Partial<AndroidSettings>) {
    err = setAndroidSettings(p);
    s = androidSettings();
  }
  const num = (e: Event) => Number((e.currentTarget as HTMLInputElement).value.replace(",", "."));
  const STATUS: Record<string, string> = { ONLINE: tk("android.stOnline"), CONNECTING: tk("android.stConnecting"), SEARCHING: tk("android.stSearching"),
    NO_PERMISSION: tk("android.stNoPermission"), NO_ANSWER: tk("android.stNoAnswer"), STOPPED: tk("android.stStopped") };
</script>

{#if s}
  <section class="panel box" data-testid="android">
    <h3>{t("android.title")}</h3>
    <p class="muted small">{t("android.intro")}</p>
    <p class="small">{t("android.status")}: <strong>{tx(STATUS[s.status ?? "STOPPED"] ?? s.status ?? "")}</strong>
      {#if s.master}· {t("android.master", { ev: s.master.join("/"), db: s.masterValue == null ? "–" : s.masterValue === 0 ? "−∞" : masterDb(s.masterValue).toFixed(1) })}
      {:else if s.status === "ONLINE"}· <span class="warn">{t("android.noMaster")}</span>{/if}</p>

    <div class="choice" role="radiogroup" aria-label={t("android.volume")}>
      {#each MODES as m}
        <label class="opt" class:on={s.volumeMode === m.v} class:dis={!usable(m.v)}>
          <input type="radio" name="vol" value={m.v} checked={s.volumeMode === m.v} disabled={!usable(m.v)} onchange={() => put({ volumeMode: m.v })} />
          <span><strong>{tx(m.t)}</strong><small class="muted">{tx(m.d)}</small>
            {#if m.v === "ABSOLUTE" && !s.absoluteAvailable}<small class="warn">{t("android.volAbsoluteSystem")}</small>{/if}</span>
        </label>
      {/each}
    </div>
    {#if s.volumeMode === "ABSOLUTE"}
      <p class="small">{s.absoluteActive ? t("android.absOn") : t("android.absOff")}
        {#if s.absoluteError}· <span class="warn">{s.absoluteError}</span>{/if}</p>
    {/if}
    {#if s.volumeMode !== "OFF"}
      <div class="fields">
        <label class="fld"><span>{t("android.range")}</span>
          <span class="unit"><input class="num short" value={s.rangeDb} inputmode="numeric" onchange={(e) => put({ rangeDb: num(e) })} /><i>dB</i>
            <em>{t("android.rangeHint")}</em></span></label>
        {#if s.volumeMode !== "MIRROR" && s.volumeMode !== "ABSOLUTE"}
          <label class="fld"><span>{t("android.step")}</span>
            <span class="unit"><input class="num short" value={String(s.stepDb).replace(".", ",")} inputmode="decimal" onchange={(e) => put({ stepDb: num(e) })} /><i>dB</i>
              <em>{t("android.stepHint")}</em></span></label>
        {/if}
      </div>
    {/if}
    <label class="row">
      <span><strong>{t("android.autostart")}</strong><small class="muted">{t("android.autostartHint")}</small></span>
      <input type="checkbox" class="toggle" checked={s.autostart} onchange={(e) => put({ autostart: e.currentTarget.checked })} />
    </label>
    <p class="muted small">{t("android.api")}</p>
    {#if err}<p class="warn small">{err}</p>{/if}
  </section>

  {#if u}
    <section class="panel box" data-testid="android-update">
      <h3>{t("android.update")}</h3>
      <p class="small">{t("android.updateCurrent", { v: u.current })}
        {#if u.state === "CURRENT"}· {t("android.updateNone")}
        {:else if u.state === "AVAILABLE" || u.state === "PERMISSION"}· <strong>{t("android.updateAvailable", { v: u.latest ?? "" })}</strong>
        {:else if u.state === "CHECKING"}· {t("android.updateChecking")}
        {:else if u.state === "DOWNLOADING"}· {t("android.updateDownloading", { p: u.progress })}
        {:else if u.state === "INSTALLING"}· {t("android.updateInstalling")}{/if}</p>
      {#if u.state === "PERMISSION"}<p class="small warn">{t("android.updatePermission")}</p>{/if}
      {#if u.state === "ERROR"}<p class="small warn">{t("android.updateError", { e: u.error })}</p>{/if}
      <div class="btns">
        <button disabled={busy} onclick={() => { checkAndroidUpdate(); u = androidUpdate(); }}>{t("android.updateCheck")}</button>
        {#if u.state === "AVAILABLE" || u.state === "PERMISSION" || (u.state === "ERROR" && u.latest)}
          <button class="primary" onclick={() => { installAndroidUpdate(); u = androidUpdate(); }}>{t("android.updateInstall", { v: u.latest ?? "" })}</button>
        {/if}
      </div>
      <p class="muted small">{t("android.updateHint")}</p>
    </section>
  {/if}
{/if}

<style>
  .box { padding: .9rem 1rem; display: grid; gap: .8rem; margin-bottom: .75rem; max-width: 46rem; }
  h3 { margin: 0; }
  .small { font-size: .85rem; margin: 0; }
  .warn { color: var(--warn); }
  .choice { display: grid; gap: .5rem; grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr)); }
  .opt { display: flex; gap: .6rem; align-items: flex-start; padding: .6rem .75rem; border: 1px solid var(--line); border-radius: 10px; background: var(--raised); cursor: pointer; }
  .opt.on { border-color: color-mix(in srgb, var(--accent) 60%, transparent); background: color-mix(in srgb, var(--accent) 8%, var(--panel)); }
  .opt.dis { opacity: .55; cursor: not-allowed; }
  .opt input { margin-top: .2rem; accent-color: var(--accent); }
  .opt span { display: grid; gap: .1rem; }
  small { font-size: .78rem; }
  .fields { display: grid; gap: .6rem 1.25rem; grid-template-columns: repeat(auto-fill, minmax(min(100%, 15rem), 1fr)); }
  .fld { display: grid; gap: .15rem; font-size: .8rem; color: var(--muted); }
  .unit { display: flex; align-items: baseline; gap: .4rem; flex-wrap: wrap; }
  .unit i { font-style: normal; color: var(--muted); }
  .unit em { font-style: normal; color: var(--faint); font-size: .75rem; flex-basis: 100%; }
  .short { width: 5.5rem; }
  .row { display: flex; justify-content: space-between; align-items: center; gap: 1rem; }
  .row span { display: grid; gap: .1rem; }
  .btns { display: flex; gap: .5rem; flex-wrap: wrap; }
  .toggle { width: 1.2rem; height: 1.2rem; accent-color: var(--accent); flex: none; }
</style>
