#!/usr/bin/env python3
"""Test and stress suite of the Android app with the DEV Forge (PLAN.md, #33).

    python3 tests.py [T01 T02 …] [--soak MINUTES] [--cycles N] [--requests N]

Results: results/<timestamp>/report.md, logcat.txt, data.json. Every write is read back."""
import argparse, json, os, random, re, statistics, subprocess, sys, threading, time

import rig

HERE = os.path.dirname(os.path.abspath(__file__))
FW = os.path.expanduser("~/avinavant/work/forge-firmware")
SINK = "alsa_output.usb-Forge_DSP_Forge_DSP_96ELN_-_Audio_2U2971090C000100-00.analog-stereo"
FREE_EVENT = 74          # an event no block of the set-up uses
VOL_BLOCK, VOL_EVENT = 24, 73
PINK_CSV = os.path.join(HERE, "results", "pink-spectra.csv")
BANDS = [25 * 2 ** (i / 3) for i in range(31)]


def pink_rows(t0, t1):
    rows = []
    try:
        with open(PINK_CSV) as f:
            for line in f:
                v = line.split(",")
                if len(v) == 65 and t0 <= float(v[0]) <= t1:
                    rows.append([float(x) for x in v])
    except FileNotFoundError:
        pass
    return rows


NO_SIGNAL_DBFS = -36.0   # line-in noise floor about -42.5 dBFS (46 %); below this nothing arrives


def signal_missing(r, test):
    """true (and a failed precondition) when only the line-in's noise arrives"""
    lv = level_at(time.time() - 10, time.time())
    if lv and max(lv) >= NO_SIGNAL_DBFS:
        return False
    r.check(test, "Signal am Line-In (über dem Grundrauschen)", False, f"{lv} dBFS")
    return True


def level_at(t0, t1):
    """broadband level L/R (dBFS) of outputs 1/2 at the line-in, median over the window"""
    r = pink_rows(t0, t1)
    return [statistics.median(x[1] for x in r), statistics.median(x[2] for x in r)] if r else None


def spectrum(t0, t1):
    """third-octave spectrum L, R (power average) of the window"""
    r = pink_rows(t0, t1)
    if not r:
        return None
    import numpy as np
    a = np.array(r)
    return (10 * np.log10(np.mean(10 ** (a[:, 3:34] / 10), 0)), 10 * np.log10(np.mean(10 ** (a[:, 34:65] / 10), 0)))


def audio_events(t0, t1, drop_db=20):
    """1-s blocks in which the noise at output 1 or 2 is more than drop_db below the level before the window"""
    ref = level_at(t0 - 20, t0 - 1)
    r = pink_rows(t0, t1)
    if not r or not ref:
        return None
    drop = [x[0] for x in r if x[1] < ref[0] - drop_db or x[2] < ref[1] - drop_db]
    return {"blocks": len(r), "dropouts": len(drop), "first_drop": drop[0] if drop else None}


_model_mod = None


def band_model(params):
    """third-octave response of a biquad chain by the firmware's formulas (tests/test_cmd.py)"""
    global _model_mod
    import numpy as np
    if _model_mod is None:
        sys.path.insert(0, os.path.join(FW, "tests")); os.environ.setdefault("NATIVE_DIR", "/nonexistent")
        import test_cmd as _t; _model_mod = _t
    out = []
    for c in BANDS:
        fr = np.geomspace(c / 2 ** (1 / 6), c * 2 ** (1 / 6), 9); h = np.ones(len(fr), complex)
        for p in params:
            raw = " ".join(f"{v:08X}" for v in _model_mod.bq_reference("V40-96ELN", *p))
            h *= np.array([_model_mod.bq_response(raw, f) for f in fr])
        out.append(10 * np.log10(np.mean(np.abs(h) ** 2)))
    return np.array(out)


class Run:
    def __init__(self):
        self.dir = os.path.join(HERE, "results", time.strftime("%Y%m%d-%H%M%S"))
        os.makedirs(self.dir, exist_ok=True)
        self.lines, self.data, self.verdicts, self.shots, self.windows = [], {}, [], [], []
        rig.logcat_clear()

    def say(self, *a):
        l = " ".join(str(x) for x in a); print(l, flush=True); self.lines.append(l)

    def check(self, test, what, ok, detail=""):
        self.verdicts.append((test, what, bool(ok), detail))
        self.say(f"   {'OK    ' if ok else 'FEHLER'} {what}" + (f" ({detail})" if detail else ""))
        return ok

    def shot(self, name, caption, page=None, cdp=None):
        """screenshot of the Forge app (optionally of one GUI page); skipped if something else has the focus"""
        rig.sh(f"am start -n {rig.PKG}/.ui.MainActivity >/dev/null"); time.sleep(3)
        if page and cdp is not None:
            try:
                # only a page with data: wait until the GUI has read the Forge (status online, all channels)
                t0 = time.time()
                while time.time() - t0 < 300:
                    st = cdp.js("window.__forgeRig.app.status + ':' + Object.keys(window.__forgeRig.app.device.biquads).length")
                    if st and st.startswith("online:") and int(st.split(":")[1]) >= 1:
                        break
                    time.sleep(5)
                else:
                    self.say(f"   (Screenshot {name}: GUI nach 300 s nicht fertig eingelesen, {st})")
                cdp.js(f"setTimeout(() => {{ window.__forgeRig.app.page = '{page}' }}, 0); 1"); time.sleep(8)
            except Exception:
                pass
        focus = rig.sh("dumpsys window | grep -m1 mCurrentFocus")
        if rig.PKG not in focus:
            self.say(f"   (kein Screenshot {name}: im Vordergrund {focus.strip()[-80:]})"); return
        p = os.path.join(self.dir, f"{len(self.shots) + 1:02d}-{name}.png")
        if rig.screenshot(p):
            self.shots.append((os.path.basename(p), caption))

    def window(self, test, t0, t1, expect_drop=False):
        """audio over a test (pink noise at outputs 1/2): it must run through, unless the test mutes or restarts"""
        ev = audio_events(t0, t1)
        self.windows.append((test, t0, t1, ev, expect_drop))
        if ev is None:
            self.say(f"   (kein Audio-Monitor für {test})"); return
        lv = level_at(t0, t1)
        if not lv or max(lv) < NO_SIGNAL_DBFS:
            # only the line-in's own noise: there is nothing that could drop out, no statement possible
            self.say(f"   (kein Signal am Line-In während {test}: {lv} dBFS, Durchlauf nicht prüfbar)"); return
        if expect_drop:
            self.say(f"   Audio {test}: {ev['dropouts']} Sekunden mit Einbruch > 20 dB (in diesem Test erwartet)")
        else:
            self.check(test, "Audio läuft durch (kein Einbruch > 20 dB)", ev["dropouts"] == 0, f"{ev['dropouts']} von {ev['blocks']} s")

    def save(self):
        crash = rig.logcat(r"FATAL EXCEPTION|ANR in " + re.escape(rig.PKG) + r"|AndroidRuntime.*" + re.escape(rig.PKG))
        open(os.path.join(self.dir, "logcat.txt"), "w").write(rig.adb("logcat", "-d", check=False))
        self.data["shots"] = self.shots
        self.data["windows"] = self.windows
        json.dump(self.data, open(os.path.join(self.dir, "data.json"), "w"), indent=1)
        ok = sum(v[2] for v in self.verdicts)
        rep = [f"# Testlauf {os.path.basename(self.dir)}", "", f"{ok}/{len(self.verdicts)} Prüfungen bestanden, "
               f"{len(crash)} Absturz-/ANR-Zeilen im logcat", "", "| Test | Prüfung | Ergebnis | Detail |", "|---|---|---|---|"]
        rep += [f"| {t} | {w} | {'OK' if o else '**FEHLER**'} | {d} |" for t, w, o, d in self.verdicts]
        rep += ["", "## Protokoll", "", "```", *self.lines, "```"]
        if crash:
            rep += ["", "## Abstürze", "", "```", *crash[:50], "```"]
        open(os.path.join(self.dir, "report.md"), "w").write("\n".join(rep) + "\n")
        self.say(f"Bericht: {self.dir}/report.md")


def services():
    # only the Forge service (the WebView's sandboxed renderer runs as a service of the app as well)
    return rig.sh(f"dumpsys activity services {rig.PKG} | grep -c 'ServiceRecord.*ForgeService'").strip()


def mem_kb():
    m = re.search(r"TOTAL PSS:\s+(\d+)|TOTAL\s+(\d+)", rig.sh(f"dumpsys meminfo {rig.PKG}"))
    return int(next(g for g in m.groups() if g)) if m else None


def threads():
    pid = rig.sh(f"pidof {rig.PKG}").strip().split()
    return int(rig.sh(f"ls /proc/{pid[0]}/task | wc -l").strip()) if pid else None


# --- T01 -----------------------------------------------------------------------------------------
def t01(r, f):
    r.say("T01 Erstes Anstecken (App beendet, Forge ab/an, Dialog, Handshake)")
    times = []
    for k in range(3):
        rig.unplug(); rig.sh(f"am force-stop {rig.PKG}"); time.sleep(3)
        rig.plug(); t0 = time.time()
        rig.accept_usb_dialog(wait=20)
        t = rig.wait_online(f, 60)
        times.append(None if t is None else round(time.time() - t0, 1))
    r.data["T01"] = times
    r.check("T01", "Dienst nach dem Anstecken verbunden (3×)", all(t is not None for t in times), f"Sekunden {times}")
    r.check("T01", "verbunden in < 45 s (VM, arm64 emuliert, inkl. Dialog)", all(t is not None and t < 45 for t in times))
    r.check("T01", "genau ein Dienst", services() == "1", services())


# --- T02 -----------------------------------------------------------------------------------------
def t02(r, f, cycles):
    r.say(f"T02 Hotplug: {cycles} Zyklen, Pausen 0,3–10 s, teils mit Anfragen während des Abziehens")
    rnd = random.Random(2); back, hung = [], 0
    for k in range(cycles):
        busy = None
        if rnd.random() < 0.5:   # a request on its way while the Forge goes
            res = {}
            busy = threading.Thread(target=lambda: res.update(a=f.send("get.1 biquad.1", timeout=20))); busy.start()
            time.sleep(rnd.uniform(0, 0.5))
        rig.unplug(); time.sleep(rnd.uniform(0.3, 10)); rig.plug()
        if busy:
            busy.join(25); hung += busy.is_alive()
        t = rig.wait_online(f, 45)
        back.append(t)
        if t is None:
            r.say(f"   Zyklus {k + 1}: nicht wieder verbunden")
    ok = [t for t in back if t is not None]
    r.data["T02"] = back
    r.check("T02", f"nach jedem Anstecken wieder verbunden", len(ok) == cycles, f"{len(ok)}/{cycles}")
    if ok:
        r.check("T02", "Wiederverbinden < 15 s (Median)", statistics.median(ok) < 15,
                f"Median {statistics.median(ok):.1f} s, max {max(ok):.1f} s")
    r.check("T02", "keine hängende Anfrage", hung == 0, f"{hung} hängend")
    r.check("T02", "genau ein Dienst", services() == "1", services())


# --- T03 -----------------------------------------------------------------------------------------
def t03(r, f, n):
    r.say(f"T03 Befehlsdurchsatz: {n} gemischte Anfragen über den CommandReceiver")
    rnd = random.Random(3); lat, wrong, lost, setbad = [], 0, 0, 0
    for k in range(n):
        kind = rnd.random()
        if kind < 0.3:
            v = str(rnd.randint(1, 250)); t0 = time.time()
            err, _ = f.set(f"event.{FREE_EVENT}.value", v); lat.append(time.time() - t0)
            if err:
                setbad += 1; r.say(f"   {time.strftime('%H:%M:%S')} set event.{FREE_EVENT}.value={v}: {err} ({time.time() - t0:.1f} s)")
            continue
        reg = rnd.choice([f"gain.out.{rnd.randint(1, 8)}", f"delay.{rnd.randint(1, 8)}", f"modc.{rnd.choice([20, 21, 22, 23])}",
                          f"biquad.{rnd.randint(1, 8)}.{rnd.randint(1, 4)}", "mixer.in", "preset.loaded", "system.kmh"])
        t0 = time.time(); err, lines = f.send(f"get.1 {reg}"); lat.append(time.time() - t0)
        if err or not lines:
            lost += 1; r.say(f"   {time.strftime('%H:%M:%S')} get {reg}: {err or 'leer'} ({time.time() - t0:.1f} s)")
        elif not any(f"<.1 {reg}=" in l for l in lines):
            wrong += 1; r.say(f"   falsche Antwort auf {reg}: {lines[:2]}")
    r.data["T03"] = {"latency": lat}
    lat.sort()
    r.check("T03", "keine vertauschte Antwort", wrong == 0, f"{wrong}/{n}")
    r.check("T03", "keine verlorene Antwort", lost == 0, f"{lost}/{n}")
    r.check("T03", "jedes set zurückgelesen", setbad == 0, f"{setbad} Abweichungen")
    r.say(f"   Laufzeit je Anfrage (inkl. adb/am in der VM): Median {statistics.median(lat) * 1000:.0f} ms, "
          f"99 % {lat[int(len(lat) * 0.99) - 1] * 1000:.0f} ms")


# --- T04 -----------------------------------------------------------------------------------------
def master():
    return None


def t04(r, f, cdp):
    r.say("T04 Lautstärke: Lautstärkeblock 24 (Event 73) als Master, Modi MIRROR / MIRROR_FIXED / REMOTE")
    e, _ = f.set(f"modc.{VOL_BLOCK}", f"[1,16,FF000000,{VOL_EVENT},{VOL_EVENT},0,0,00000000]")
    r.check("T04", "Lautstärkeblock angelegt (zurückgelesen)", e == "", e)
    cdp.js("window.ForgeAndroid.refreshMaster()"); time.sleep(3)
    s = rig.app_settings(cdp)
    r.check("T04", "App kennt den Master", s.get("master") == [VOL_EVENT], str(s.get("master")))

    def ev():
        return int(f.get(f"event.{VOL_EVENT}.value")[0].get(f"event.{VOL_EVENT}.value", "-1"))

    def idx():
        m = re.search(r"volume is (\d+) in range \[(\d+)\.\.(\d+)\]", rig.sh("cmd media_session volume --stream 3 --get"))
        return (int(m.group(1)), int(m.group(3))) if m else (None, None)

    def mode(m, **kw):
        return cdp.js(f"window.ForgeAndroid.setSettings(JSON.stringify({json.dumps(dict(volumeMode=m, **kw))}))")

    # MIRROR: master = 255 + 2 * (-range * (1 - i/max))
    r.check("T04", "Modus MIRROR gesetzt", mode("MIRROR", rangeDb=60) == "")
    bad = []
    for i in (25, 20, 12, 5, 1, 0, 15):
        rig.sh(f"cmd media_session volume --stream 3 --set {i}"); time.sleep(1.5)
        cur, mx = idx()
        want = 0 if cur == 0 else max(0, min(255, 255 + round(2 * (-60 * (1 - cur / mx)))))
        got = ev()
        if got != want:
            bad.append((cur, want, got))
    r.check("T04", "MIRROR: Master folgt der Medienlautstärke", not bad, f"Abweichungen {bad}" if bad else "7 Stufen")
    # key burst: 15 x up as fast as adb allows, then the end value must match
    rig.sh("cmd media_session volume --stream 3 --set 5"); time.sleep(1)
    rig.sh("for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do input keyevent 24; done"); time.sleep(3)
    cur, mx = idx(); want = 255 + round(2 * (-60 * (1 - cur / mx)))
    got = ev()
    r.check("T04", "MIRROR: Tastenstoß (15× lauter), Endwert stimmt", got == want, f"Index {cur}/{mx}, Master {got}, Soll {want}")
    # MIRROR_FIXED: Android parks one below top, each key = stepDb on the master
    r.check("T04", "Modus MIRROR_FIXED gesetzt", mode("MIRROR_FIXED", rangeDb=60, stepDb=1.0) == "")
    time.sleep(2)
    f.set(f"event.{VOL_EVENT}.value", "215"); cdp.js("window.ForgeAndroid.refreshMaster()"); time.sleep(2)
    base = ev()
    rig.sh("input keyevent 24; sleep 0.3; input keyevent 24; sleep 0.3; input keyevent 24"); time.sleep(2)
    up = ev()
    rig.sh("for i in 1 2 3 4 5; do input keyevent 25; sleep 0.3; done"); time.sleep(2)
    down = ev()
    cur, mx = idx()
    r.check("T04", "MIRROR_FIXED: 3× lauter = +3 dB", up - base == 6, f"{base} -> {up}")
    r.check("T04", "MIRROR_FIXED: 5× leiser = −5 dB", up - down == 10, f"{up} -> {down}")
    r.check("T04", "MIRROR_FIXED: Android wieder eine Stufe unter Maximum", cur == mx - 1, f"Index {cur}/{mx}")
    # signal: master -10 dB must lower every output by 10 dB in the DSP (tone from the PC)
    tone = os.path.join(HERE, "results", "t04_1k.wav")
    if not os.path.exists(tone):
        subprocess.run([sys.executable, os.path.join(FW, "tools/measure/forge_measure.py"), "tone", tone, "--freq", "1234",
                        "--db", "-20", "--seconds", "30"], check=True, capture_output=True)
    p = subprocess.Popen(["pw-play", "--target", SINK, tone])
    try:
        def vu():
            f.get("system.vu.out"); time.sleep(1)
            acc = []
            for _ in range(3):
                v = f.get("system.vu.out")[0].get("system.vu.out", "[]").strip("[]")
                acc.append([int(x) * 0.5 for x in v.split(",") if x]); time.sleep(0.4)
            return [sum(c) / len(c) for c in zip(*acc)] if acc and acc[0] else []
        f.set(f"event.{VOL_EVENT}.value", "255"); time.sleep(2); a = vu()
        f.set(f"event.{VOL_EVENT}.value", "235"); time.sleep(2); b = vu()
        d = [round(x - y, 1) for x, y in zip(a, b) if x > 30]
        r.data["T04_vu"] = {"0dB": a, "-10dB": b}
        r.check("T04", "Master −10 dB senkt die Ausgänge um 10 dB (DSP-Pegel, ±1 dB)", d and all(abs(x - 10) <= 1 for x in d), f"{d}")
    finally:
        p.terminate(); p.wait()
    mode("OFF")
    e, _ = f.set(f"modc.{VOL_BLOCK}", "[0,0,00000000,0,0,0,0,00000000]", verify=False)
    f.set(f"event.{VOL_EVENT}.value", "255", verify=False)


# --- T05 -----------------------------------------------------------------------------------------
def t05(r, f, cdp, seconds=120):
    r.say(f"T05 Gleichzeitigkeit {seconds} s: GUI schreibt, CommandReceiver fragt, Lautstärketasten, Keepalive")
    stop = threading.Event(); errs = {"cmd": 0, "wrong": 0}
    model = {}

    def cmd_load():
        rnd = random.Random(5)
        while not stop.is_set():
            reg = rnd.choice(["gain.out.3", "delay.5", "modc.21", "biquad.2.1", "preset.loaded"])
            e, lines = f.send(f"get.1 {reg}", timeout=20)
            if e:
                errs["cmd"] += 1
            elif not any(f"<.1 {reg}=" in l for l in lines):
                errs["wrong"] += 1

    def keys():
        while not stop.is_set():
            rig.sh("input keyevent 24; input keyevent 25"); time.sleep(0.5)

    th = [threading.Thread(target=cmd_load, daemon=True), threading.Thread(target=keys, daemon=True)]
    [t.start() for t in th]
    rnd = random.Random(6); t0 = time.time(); writes = 0
    while time.time() - t0 < seconds:   # the GUI's own write path (throttled queue), as a slider would
        o = rnd.randint(1, 8); v = rnd.randint(200, 229)
        cdp.js(f"(() => {{ const a = window.__forgeRig.app; a.device.gain.out[{o - 1}] = {v}; window.__forgeRig.write('gain.out.{o}', {v}); }})()")
        model[f"gain.out.{o}"] = str(v); writes += 1
        time.sleep(rnd.uniform(0.02, 0.3))
    time.sleep(3); stop.set(); [t.join(30) for t in th]
    time.sleep(2)
    have = {}
    for o in range(1, 9):
        have.update(f.get(f"gain.out.{o}")[0])
    bad = {k: (v, have.get(k)) for k, v in model.items() if have.get(k) != v}
    r.check("T05", f"GUI-Schreiben: Endwerte im Forge ({writes} Schreibvorgänge)", not bad, str(bad) if bad else "")
    r.check("T05", "CommandReceiver: keine vertauschte Antwort", errs["wrong"] == 0, str(errs))
    r.check("T05", "CommandReceiver: keine verlorene Antwort", errs["cmd"] == 0, str(errs))
    err = f.get("system.errors")[0].get("system.errors")
    r.check("T05", "Forge ohne Fehlerzähler", err == "[]", err)
    for k, v in model.items():
        f.set(k, v)


# --- T06 -----------------------------------------------------------------------------------------
def t06(r, f, cdp):
    r.say("T06 Speichern über die GUI: MODC und Filter in der GUI ändern, Forge- und App-Neustart")
    m21 = f.get("modc.21")[0]["modc.21"]
    js = """(async () => { const R = window.__forgeRig, d = R.app.device; const b = R.copyModc(d.modc[21]);
      d.modc[21].flags[3] = 21; d.modc[21].sets[0] = 120; const e = await R.sendModc(21, b);
      d.biquads[3][5] = { ...d.biquads[3][5], f: 260, gain: -33 };
      await R.writeRaw('biquad.3.5.calc', '[' + [d.biquads[3][5].type, 260, -33, d.biquads[3][5].q, d.biquads[3][5].b, d.biquads[3][5].flags].join(',') + ']');
      return e; })()"""
    e = cdp.js(js)
    r.check("T06", "GUI hat geschrieben", e == "", e)
    want21 = f.get("modc.21")[0]["modc.21"]; want_bq = f.get("biquad.3.5")[0]["biquad.3.5"]
    r.check("T06", "Forge hat die GUI-Werte", want21 != m21 and ",260,-33," in want_bq, f"{want21} {want_bq}")
    # Forge restart: CP2102 to the PC, bootloader + start, back to the VM
    restart_forge(r)
    t = rig.wait_online(f, 60)
    r.check("T06", "nach Forge-Neustart verbunden", t is not None)
    r.check("T06", "MODC-Wert nach Neustart erhalten", f.get("modc.21")[0].get("modc.21") == want21)
    r.check("T06", "Filter nach Neustart erhalten", f.get("biquad.3.5")[0].get("biquad.3.5") == want_bq)
    rig.sh(f"am force-stop {rig.PKG}"); time.sleep(2); c2 = rig.open_gui(); time.sleep(8)
    n = c2.js("window.__forgeRig ? window.__forgeRig.app.device.modc[21] && window.__forgeRig.modcText(window.__forgeRig.app.device.modc[21]) : null")
    r.check("T06", "GUI liest nach App-Neustart dieselben Werte", n is not None and n in want21, f"{n}")
    f.set("modc.21", m21)


def restart_forge(r):
    """the Forge restarts: CP2102 to the PC, bootloader by command, start, back to the VM"""
    rig.unplug(); time.sleep(4)
    c = [sys.executable, "-m", "c45b3", "--port", "/dev/ttyUSB0", "--app-baud", "38400", "--wait", "30",
         "--enter", "set.1 system.boot=1", "start"]
    p = subprocess.run(c, cwd=os.path.join(FW, "tools/c45b3-host"), capture_output=True, text=True, timeout=120)
    r.say(f"   Forge neu gestartet: {p.stdout.strip().splitlines()[-1] if p.stdout.strip() else p.stderr.strip()[-120:]}")
    time.sleep(20); rig.plug()


# --- T07 -----------------------------------------------------------------------------------------
def t07(r, f):
    r.say("T07 Lebenszyklus: Prozess töten, App beenden, Bildschirm aus, Doze")
    pid = rig.sh(f"pidof {rig.PKG}").strip()
    rig.sh(f"kill -9 {pid}" if pid else "true"); time.sleep(2)
    rig.sh(f"am startservice -n {rig.PKG}/.service.ForgeService >/dev/null 2>&1; am start-foreground-service -n {rig.PKG}/.service.ForgeService >/dev/null 2>&1")
    t = rig.wait_online(f, 60)
    r.check("T07", "nach Prozessende wieder verbunden", t is not None, f"{t and round(t, 1)} s")
    rig.sh("input keyevent 26"); time.sleep(2)   # screen off
    rig.sh("dumpsys deviceidle force-idle >/dev/null"); time.sleep(25)
    ok, e = f.online()
    r.check("T07", "Bildschirm aus + Doze: Dienst antwortet", ok, e)
    rig.sh("dumpsys deviceidle unforce >/dev/null"); rig.wake(); time.sleep(2)


# --- T08 -----------------------------------------------------------------------------------------
def t08(r, f):
    r.say("T08 Forge-Neustart während der Verbindung")
    restart_forge(r)
    t = rig.wait_online(f, 60)
    r.check("T08", "App verbindet nach Forge-Neustart neu", t is not None, f"{t and round(t, 1)} s")
    r.check("T08", "genau ein Dienst", services() == "1", services())


# --- T10 -----------------------------------------------------------------------------------------
def t10(r, f, cdp, minutes):
    r.say(f"T10 Dauerlauf {minutes} min: Mischung aus Hotplug, Anfragen, GUI-Schreiben, Tasten")
    t_end = time.time() + minutes * 60; rnd = random.Random(10); samples = []; k = 0
    while time.time() < t_end:
        k += 1
        op = rnd.choice(["req"] * 6 + ["gui"] * 3 + ["keys", "plug"])
        if op == "req":
            reg = rnd.choice(["gain.out.1", "modc.22", "biquad.4.2", "delay.3"]); e, l = f.send(f"get.1 {reg}")
            if e or not any(f"<.1 {reg}=" in x for x in l):
                r.say(f"   {time.strftime('%H:%M:%S')} Anfrage {reg}: {e or l[:1]}")
        elif op == "gui":
            try:
                v = rnd.randint(205, 229)
                cdp.js(f"window.__forgeRig.write('gain.out.2', {v})")
            except Exception as ex:
                cdp = rig.open_gui()
        elif op == "keys":
            rig.sh("input keyevent 24; input keyevent 25")
        else:
            rig.unplug(); time.sleep(rnd.uniform(0.5, 5)); rig.plug(); rig.wait_online(f, 45)
        if k % 25 == 0:
            samples.append((round(time.time()), mem_kb(), threads()))
    r.data["T10"] = samples
    if len(samples) >= 2:
        m0, m1 = samples[0][1], samples[-1][1]; t0, t1 = samples[0][2], samples[-1][2]
        r.check("T10", "Speicher stabil (< +20 %)", m0 and m1 and m1 < m0 * 1.2, f"{m0} -> {m1} kB")
        r.check("T10", "Threads stabil (< +10)", t0 and t1 and t1 - t0 < 10, f"{t0} -> {t1}")
    err = f.get("system.errors")[0].get("system.errors")
    r.check("T10", "Forge ohne Fehlerzähler", err == "[]", err)


# --- T11 -----------------------------------------------------------------------------------------
ROUTE_FLOOR = None


def find_route(f):
    """which output reaches which channel of the PC line-in (the DEV cabling changes): each output alone"""
    for o in range(1, 9):
        f.set(f"mute.out.{o}", "1")
    time.sleep(3); t = time.time(); time.sleep(8); floor = level_at(t, time.time())
    global ROUTE_FLOOR
    ROUTE_FLOOR = spectrum(t, time.time())
    best = (None, None, -1e9)
    for o in range(1, 9):
        f.set(f"mute.out.{o}", "0"); time.sleep(3); t = time.time(); time.sleep(5)
        lv = level_at(t, time.time()); f.set(f"mute.out.{o}", "1")
        for c in (0, 1):
            if lv and floor and lv[c] - floor[c] > best[2]:
                best = (o, c, lv[c] - floor[c])
    return best


def t11(r, f, cdp):
    """filters, EQ, gain, mute, delay and presets live on the pink noise: every change through the GUI's own
    write path; filters/EQ in the third-octave spectrum against the firmware formulas, the rest broadband"""
    import numpy as np
    r.say("T11 Filter/EQ/Gain/Mute/Delay live am Signal (rosa Rauschen, Änderungen über die GUI)")
    if level_at(time.time() - 3, time.time()) is None:
        r.check("T11", "Audio-Monitor läuft", False, "keine Spektren"); return
    if signal_missing(r, "T11"):
        return
    mutes = {o: f.get(f"mute.out.{o}")[0].get(f"mute.out.{o}") for o in range(1, 9)}
    OUT, CH, snr = find_route(f)
    r.say(f"   Messweg: Ausgang {OUT} -> Line-In {'LR'[CH]} ({snr:.0f} dB über dem Rauschen); die anderen Ausgänge stumm")
    r.data["T11_route"] = [OUT, "LR"[CH], snr]
    f.set(f"mute.out.{OUT}", "0"); time.sleep(4)
    chain = []
    for k, v in f.get(f"biquad.{OUT}")[0].items():
        t, fr, g, qq, bb, fl = map(int, v.strip("[]").split(","))
        if t and fl & 0x80:
            chain.append((t, fr, g, qq, bb, fl))
    passband = band_model(chain) > -12   # where the output's own crossover lets the noise through
    W = "window.__forgeRig.writeRaw"
    SL = [10, 11]
    cdp.js(f"setTimeout(() => {{ window.__forgeRig.app.page = 'filters' }}, 0); 1"); time.sleep(6)

    def filt(name, params, shot=None):
        time.sleep(4); a = spectrum(time.time() - 10, time.time())
        t0 = time.time()
        cdp.js("(async () => { " + "; ".join(f"await {W}('biquad.{OUT}.{n}.calc', '[{','.join(map(str, p))}]')" for n, p in zip(SL, params)) + " })()")
        time.sleep(13); b = spectrum(time.time() - 10, time.time())
        if shot:
            r.shot(shot[0], shot[1], page="filters", cdp=cdp)
        cdp.js("(async () => { " + "; ".join(f"await {W}('biquad.{OUT}.{n}.calc', '[0,0,0,0,0,0]')" for n in SL[:len(params)]) + " })()")
        time.sleep(4)
        dl = b[CH] - a[CH]
        mod = band_model(params)
        snr = a[CH] - ROUTE_FLOOR[CH]
        use = passband & np.array([c >= 40 for c in BANDS]) & (snr > 15) & (mod > -(snr - 6))
        worst = float(np.abs(dl - mod)[use].max()) if use.any() else float("nan")
        r.data.setdefault("T11", []).append({"step": name, "t0": t0, "bands": BANDS, "dL": dl.tolist(), "dR": (b[1 - CH] - a[1 - CH]).tolist(),
                                              "model": mod.tolist(), "usable": use.tolist(), "worst": worst, "out": OUT})
        r.check("T11", name + " (Terzspektrum gegen Soll)", worst <= 2.0, f"Ausgang {OUT}, max. Abweichung {worst:.1f} dB in {int(use.sum())} Terzen")

    def broad(js, back, settle=5.0):
        a = level_at(time.time() - 3, time.time()); t0 = time.time(); cdp.js(js); time.sleep(settle)
        b = level_at(time.time() - 2, time.time()); ev = audio_events(t0, time.time())
        cdp.js(back); time.sleep(settle)
        return b[CH] - a[CH], ev

    # EQ/filters in the middle of the output's pass band
    pb = [c for c, ok in zip(BANDS, passband) if ok]
    fc = int(round(np.sqrt(pb[0] * pb[-1]) / 10) * 10) if pb else 1000
    filt(f"EQ {fc} Hz +6 dB, Q 1,4", [(1, fc, 60, 1414, 0, 128)], ("t11-eq", f"T11: Filterseite, EQ {fc} Hz +6 dB auf Ausgang {OUT}"))
    filt(f"EQ {fc} Hz −9 dB, Q 4", [(1, fc, -90, 4000, 0, 128)])
    filt(f"Höhen-Shelf {fc} Hz −6 dB", [(15, fc, -60, 707, 0, 128)])
    filt(f"Tiefpass {fc} Hz LR4", [(3, fc, 0, 707, 0, 128), (3, fc, 0, 707, 0, 128)])
    filt(f"EQ {fc} Hz +6 dB ausgeschaltet", [(1, fc, 60, 1414, 0, 0)])
    g = int(f.get(f"gain.out.{OUT}")[0][f"gain.out.{OUT}"])
    d, _ = broad(f"{W}('gain.out.{OUT}', '{g - 12}')", f"{W}('gain.out.{OUT}', '{g}')")
    r.check("T11", f"Gain Ausgang {OUT} −6 dB (breitbandig)", abs(d + 6) <= 1, f"{d:+.1f} dB")
    d, _ = broad(f"{W}('mute.out.{OUT}', '1')", f"{W}('mute.out.{OUT}', '0')")
    r.check("T11", f"Mute Ausgang {OUT}: mindestens 20 dB leiser", d <= -20, f"{d:+.1f} dB (bis zum Rauschen des Line-In)")
    dl0 = int(f.get(f"delay.{OUT}")[0][f"delay.{OUT}"])
    d, ev = broad(f"{W}('delay.{OUT}', '{dl0 + 48}')", f"{W}('delay.{OUT}', '{dl0}')")
    r.check("T11", "Delay ändern: Pegel gleich", abs(d) <= 0.5, f"{d:+.1f} dB, Einbruch {ev and ev['dropouts']} s")
    t0 = time.time()
    for gg in list(range(0, 61, 5)) + list(range(55, -1, -5)):
        cdp.js(f"window.__forgeRig.write('biquad.{OUT}.10.calc', [1,{fc},{gg},1414,0,128])"); time.sleep(0.12)
    time.sleep(3); ev = audio_events(t0, time.time())
    cdp.js(f"{W}('biquad.{OUT}.10.calc', '[0,0,0,0,0,0]')"); time.sleep(2)
    r.check("T11", "EQ-Sweep 0→+6→0 dB in 25 Schritten: Ton läuft durch", ev and ev["dropouts"] == 0, f"{ev}")
    b0 = level_at(time.time() - 3, time.time())
    f.set("preset.loaded", "0", verify=False); time.sleep(6); f.set("preset.loaded", "1", verify=False); time.sleep(8)
    for o in range(1, 9):   # the preset load restores its mutes; the measured output alone again
        f.set(f"mute.out.{o}", "0" if o == OUT else "1")
    time.sleep(4); b1 = level_at(time.time() - 3, time.time())
    f.get("system.vu"); time.sleep(1.3); g78 = f.get("system.vu")[0].get("system.vu", "")
    r.check("T11", "Presetwechsel 1→0→1: Pegel danach wie vorher (±1 dB)", b0 and b1 and abs(b1[CH] - b0[CH]) <= 1,
            f"{b1[CH] - b0[CH]:+.1f} dB, Eingänge {g78}")
    for o, v in mutes.items():
        if v is not None:
            f.set(f"mute.out.{o}", v)



# --- shared for T12..T15: measuring output found per band, its crossover bypassed through the GUI ----------
def route_by_band(f):
    """each output alone (mute.out through the app): the output/channel with the most bands far above the
    line-in's noise; returns (out, ch, floor spectrum)"""
    import numpy as np
    for o in range(1, 9):
        f.set(f"mute.out.{o}", "1")
    time.sleep(3); t = time.time(); time.sleep(8); floor = spectrum(t, time.time())
    best = (None, None, -1)
    for o in range(1, 9):
        f.set(f"mute.out.{o}", "0"); time.sleep(3); t = time.time(); time.sleep(6)
        sp = spectrum(t, time.time()); f.set(f"mute.out.{o}", "1")
        for c in (0, 1):
            n = int(np.sum(sp[c] - floor[c] > 15))
            if n > best[2]:
                best = (o, c, n)
    return best[0], best[1], floor


class Setup:
    """the measured output alone and flat (its active filters off, through the GUI); undone at the end"""
    def __init__(self, r, f, cdp):
        self.r, self.f, self.cdp = r, f, cdp
        self.mutes = {o: f.get(f"mute.out.{o}")[0].get(f"mute.out.{o}") for o in range(1, 9)}
        self.out, self.ch, self.floor = route_by_band(f)
        r.say(f"   Messweg: Ausgang {self.out} -> Line-In {'LR'[self.ch]}; alle anderen Ausgänge stumm, Weiche von Ausgang {self.out} überbrückt")
        self.saved = {}
        for k, v in f.get(f"biquad.{self.out}")[0].items():
            t, fr, g, qq, bb, fl = map(int, v.strip("[]").split(","))
            if t and fl & 0x80:
                self.saved[k] = v
                self.w(k + ".calc", f"[{t},{fr},{g},{qq},{bb},{fl & 0x7F}]")
        self.f.set(f"mute.out.{self.out}", "0")
        time.sleep(4)

    def w(self, reg, val):
        """write through the GUI's own path and read back through the app"""
        e = self.cdp.js(f"window.__forgeRig.writeRaw('{reg}', '{val}')")
        r = reg[:-5] if reg.endswith(".calc") else reg
        got = self.f.get(r)[0].get(r)
        ok = got == val or (val.startswith("[0,") and got and got.startswith("[0,"))
        if not ok:
            self.r.say(f"   Schreiben {reg}={val}: zurückgelesen {got} ({e})")
        return ok

    def undo(self):
        for k, v in self.saved.items():
            self.w(k + ".calc", v)
        for o, v in self.mutes.items():
            if v is not None:
                self.f.set(f"mute.out.{o}", v)


def spec_step(S, apply, revert, settle=5, win=10):
    """spectrum before/after an action; difference of the measured channel, level offset of flat bands removed later"""
    time.sleep(2); a = spectrum(time.time() - win, time.time())
    apply(); time.sleep(settle); t = time.time(); time.sleep(win); b = spectrum(t, time.time())
    revert(); time.sleep(settle)
    return a[S.ch], b[S.ch]


def compare_spec(r, test, name, S, a, b, mod, tol=2.0, store=None):
    import numpy as np
    d = b - a
    snr = a - S.floor[S.ch]
    use = (np.array(BANDS) >= 40) & (snr > 15) & (mod > -(snr - 6))
    flat = use & (np.abs(mod - np.median(mod[use]) if use.any() else mod) < 0.01)
    off = 0.0
    worst = float(np.abs(d - mod)[use].max()) if use.any() else float("nan")
    r.data.setdefault("spectra", []).append({"test": test, "step": name, "bands": BANDS, "dL": d.tolist(), "dR": [0.0] * len(BANDS),
                                             "model": mod.tolist(), "usable": use.tolist(), "worst": worst, "out": S.out})
    r.check(test, name, worst <= tol, f"Ausgang {S.out}, max. Abweichung {worst:.1f} dB in {int(use.sum())} Terzen")
    return worst


def shelf_model(t, fc, db):
    return band_model([(t, fc, int(round(db * 10)), 707.1, 0, 128)])


# --- T12 -----------------------------------------------------------------------------------------
def t12(r, f, cdp):
    """polarity, delay, phase shift / all-pass through the app; transfer function with phase at the line-in"""
    import numpy as np, tf
    r.say("T12 Phasendreher, Laufzeit, Phase shift (Übertragungsfunktion mit Phase, periodisches rosa Rauschen)")
    if tf.period() is None or tf.recording(time.time() - 5, time.time()) is None:
        r.check("T12", "Monitor mit periodischem Rauschen und Rohaufnahme", False); return
    if signal_missing(r, "T12"):
        return
    S = Setup(r, f, cdp)
    try:
        def abc(apply, revert, win=16):
            # ONE contiguous recording: before (A), with (B), after (C) the change; clock drift and
            # the reference come from A and C, the change from B (tf.span)
            time.sleep(2); ta = time.time(); time.sleep(win)
            tb0 = time.time(); apply(); time.sleep(win)
            tb1 = time.time(); revert(); time.sleep(win); tc1 = time.time()
            time.sleep(2)   # the last second has to be in the index
            R = tf.span(ta, tb0, tb1, tc1, S.ch)
            r.data.setdefault("drift_ppm", []).append(round(R["ppm"], 1))
            r.data.setdefault("snr_db", []).append(round(R["snr"], 1))
            return R
        deg = lambda z: np.degrees(np.angle(z))
        # polarity
        R = abc(lambda: S.w(f"phase.out.{S.out}", "1"), lambda: S.w(f"phase.out.{S.out}", "0"))
        ph = deg(np.sum(R["rb"] * R["wb"]) / np.sum(R["wb"]))
        spread = float(np.max(np.abs((deg(R["rb"]) - 180 + 180) % 360 - 180))) if len(R["rb"]) else 999
        r.check("T12", f"Phasendreher Ausgang {S.out}: 180° in allen Terzbändern (±10°)", abs(abs(ph) - 180) <= 10 and spread <= 10 and R["pol"] < 0,
                f"{ph:+.0f}°, größte Abweichung {spread:.0f}° in {len(R['fb'])} Bändern {R['fb'].min():.0f}–{R['fb'].max():.0f} Hz, SNR {R['snr']:.0f} dB")
        r.data.setdefault("phase", []).append({"step": "Phasendreher", "f": R["fb"].tolist(), "deg": deg(R["rb"]).tolist(), "model": [180.0] * len(R["fb"])})
        # delay (samples at 96 kHz)
        d0 = int(f.get(f"delay.{S.out}")[0][f"delay.{S.out}"])
        for add in (48, 96, 480):
            R = abc(lambda: S.w(f"delay.{S.out}", str(d0 + add)), lambda: S.w(f"delay.{S.out}", str(d0)))
            want = add / 96.0
            r.check("T12", f"Laufzeit +{add} Samples = {want:.2f} ms (±0,02 ms)", abs(R["delay_ms"] - want) <= 0.02,
                    f"gemessen {R['delay_ms']:.3f} ms (grob {R['coarse_ms']:.3f}), Taktversatz {R['ppm']:+.1f} ppm, SNR {R['snr']:.0f} dB")
        # phase shift / all-pass in a free slot, phase against the formulas (band values)
        _band_model_import()
        for name, p in (("Phase shift 1 kHz, Q 0,7 (Typ 20)", (20, 1000, 0, 707, 0, 128)),
                        ("Allpass 1. Ordnung 1 kHz (Typ 21)", (21, 1000, 0, 707, 0, 128)),
                        ("Allpass −45° bei 1 kHz (Typ 22)", (22, 1000, 0, 707, 0, 128))):
            R = abc(lambda: S.w(f"biquad.{S.out}.10.calc", "[" + ",".join(map(str, p)) + "]"),
                    lambda: S.w(f"biquad.{S.out}.10.calc", "[0,0,0,0,0,0]"))
            raw = " ".join(f"{v:08X}" for v in _model_mod.bq_reference("V40-96ELN", *p))
            mod = tf.band_model(R, lambda x: _model_mod.bq_response(raw, x))
            err = np.abs((deg(R["rb"]) - deg(mod) + 180) % 360 - 180)
            mag = 20 * np.log10(np.abs(R["rb"]) / np.abs(mod))
            r.data.setdefault("phase", []).append({"step": name, "f": R["fb"].tolist(), "deg": deg(R["rb"]).tolist(), "model": deg(mod).tolist()})
            r.check("T12", name + ": Phase je Terzband wie Soll (±10°), Betrag ±1 dB", float(err.max()) <= 10 and float(np.abs(mag).max()) <= 1,
                    f"größte Abweichung {err.max():.1f}° bei {R['fb'][int(np.argmax(err))]:.0f} Hz, Betrag max {np.abs(mag).max():.2f} dB, {len(R['fb'])} Bänder, SNR {R['snr']:.0f} dB")
    finally:
        S.undo()


def _band_model_import():
    band_model([])


# --- T13 / T14 / T15 -----------------------------------------------------------------------------
LD, GA = 21, 22


def t13(r, f, cdp):
    """loudness block on the measured output through the app: shelves against the formulas"""
    import numpy as np
    r.say("T13 Loudness über die App (Block 21 auf das Ausgangspaar des Messwegs)")
    if signal_missing(r, "T13"):
        return
    S = Setup(r, f, cdp)
    old = f.get(f"modc.{LD}")[0][f"modc.{LD}"]
    try:
        pair = (S.out + 1) // 2
        cfg = f"[1,108,{1 << (pair - 1):02X}D7280F,71,0,0,0,64001027]"   # ref 215, bass 0.40 @100 Hz, treble 0.15 @10 kHz
        f.set("event.71.value", "223")
        r.check("T13", "Loudness-Block über die App gesetzt (zurückgelesen)", S.w(f"modc.{LD}", cfg), cfg)
        for vol in (195, 175):
            att = (215 - vol) * 0.5
            a, b = spec_step(S, lambda: f.set("event.71.value", str(vol)), lambda: f.set("event.71.value", "223"), settle=8)
            mod = shelf_model(14, 100, min(att * 0.40, 12)) + shelf_model(15, 10000, min(att * 0.15, 12))
            compare_spec(r, "T13", f"Loudness {att:.0f} dB unter Referenz: Bass +{att * 0.40:.1f}, Höhen +{att * 0.15:.1f} dB", S, a, b, mod)
    finally:
        S.w(f"modc.{LD}", old); f.set("event.71.value", "223"); S.undo()


def t14(r, f, cdp):
    """GALA through the app on the measured output: volume and bass boost by speed, back at 0 km/h"""
    import numpy as np, threading
    r.say("T14 GALA über die App (Block 22, Geschwindigkeit per system.kmh über die App)")
    if signal_missing(r, "T14"):
        return
    S = Setup(r, f, cdp)
    old_ld, old_ga = f.get(f"modc.{LD}")[0][f"modc.{LD}"], f.get(f"modc.{GA}")[0][f"modc.{GA}"]
    stop = threading.Event()
    def feed(kmh):
        while not stop.is_set():
            f.send(f"set.1 system.kmh={kmh}", timeout=5); time.sleep(1)
    try:
        pair = (S.out + 1) // 2
        S.w(f"modc.{LD}", f"[1,108,{1 << (pair - 1):02X}D7280F,71,0,0,0,64001027]"); f.set("event.71.value", "223")
        S.w(f"modc.{GA}", f"[1,110,{1 << (S.out - 1):02X}00140C,10,20,12,10,00001E00]")   # start 20, 1 dB/10, max 6; bass 2 dB/10, max 6
        th = None
        def on():
            nonlocal th
            stop.clear(); th = threading.Thread(target=feed, args=(50,), daemon=True); th.start()
        def off():
            stop.set(); f.send("set.1 system.kmh=0", timeout=5)
        a, b = spec_step(S, on, off, settle=12)
        st = f.get("system.kmh")[0].get("system.kmh")
        mod = 3.0 + shelf_model(14, 100, 6.0)    # (50-20)/10 * 1 dB = 3 dB; bass min(3*2, 6) = 6 dB
        compare_spec(r, "T14", "GALA 50 km/h: +3 dB Pegel, +6 dB Bass-Shelf", S, a, b, mod)
        time.sleep(10); c = spectrum(time.time() - 8, time.time())[S.ch]
        back = float(np.median((c - a)[(np.array(BANDS) >= 100)]))
        r.check("T14", "GALA bei 0 km/h wieder zurück (±1 dB)", abs(back) <= 1, f"{back:+.1f} dB, system.kmh danach {st}")
    finally:
        stop.set(); S.w(f"modc.{GA}", old_ga); S.w(f"modc.{LD}", old_ld); S.undo()


def t15(r, f, cdp):
    """a complex chain through the app: crossover, EQ bands and shelves together with loudness and GALA"""
    import numpy as np, threading
    r.say("T15 Komplexe Kette über die App: Weiche + EQ + Shelves + Loudness + GALA")
    if signal_missing(r, "T15"):
        return
    S = Setup(r, f, cdp)
    old_ld, old_ga = f.get(f"modc.{LD}")[0][f"modc.{LD}"], f.get(f"modc.{GA}")[0][f"modc.{GA}"]
    chain = [(9, 60, 0, 707, 0, 128), (9, 60, 0, 707, 0, 128), (1, 250, -40, 1000, 0, 128), (1, 2500, 30, 2000, 0, 128),
             (3, 12000, 0, 707, 0, 128), (15, 8000, 20, 707, 0, 128)]
    slots = [10, 11, 12, 13, 14, 15]
    stop = threading.Event()
    def feed():
        while not stop.is_set():
            f.send("set.1 system.kmh=40", timeout=5); time.sleep(1)
    try:
        pair = (S.out + 1) // 2
        S.w(f"modc.{LD}", f"[1,108,{1 << (pair - 1):02X}D7280F,71,0,0,0,64001027]"); f.set("event.71.value", "223")
        S.w(f"modc.{GA}", f"[1,110,{1 << (S.out - 1):02X}00140C,10,20,12,10,00001E00]")
        def on():
            for n, p in zip(slots, chain):
                S.w(f"biquad.{S.out}.{n}.calc", "[" + ",".join(map(str, p)) + "]")
            f.set("event.71.value", "195"); stop.clear(); threading.Thread(target=feed, daemon=True).start()
        def off():
            stop.set(); f.send("set.1 system.kmh=0", timeout=5); f.set("event.71.value", "223")
            for n in slots:
                S.w(f"biquad.{S.out}.{n}.calc", "[0,0,0,0,0,0]")
        a, b = spec_step(S, on, off, settle=14, win=12)
        r.shot("t15-filters", f"T15: Filterseite mit der Kette auf Ausgang {S.out}", page="filters", cdp=cdp) if False else None
        att = (215 - 195) * 0.5                       # loudness: 10 dB below the reference
        gala_vol = (40 - 20) / 10 * 1.0               # +2 dB
        gala_bass = min((40 - 20) / 10 * 2.0, 6.0)    # +4 dB onto the loudness bass
        mod = band_model(chain) + shelf_model(14, 100, min(att * 0.40 + gala_bass, 12)) + shelf_model(15, 10000, att * 0.15) + gala_vol
        compare_spec(r, "T15", "Kette + Loudness (−10 dB) + GALA (40 km/h) gegen das Gesamtsoll", S, a, b, mod, tol=2.5)
    finally:
        stop.set(); S.w(f"modc.{GA}", old_ga); S.w(f"modc.{LD}", old_ld); f.set("event.71.value", "223"); S.undo()


# --- T09: firmware update through the app ---------------------------------------------------------------
def t09(r, f, cdp, local_hex=None):
    """the firmware page of the app: release 2.6.1 from GitHub, then a local file (the current state)"""
    r.say("T09 Firmware-Update über die App (Firmware-Seite: Release laden bzw. Datei wählen, Update starten)")
    probe = {k: f.get(k)[0].get(k) for k in ("modc.21", "biquad.3.5", "gain.out.3", "delay.7", "preset.loaded")}
    cdp.js("window.confirm = () => true; setTimeout(() => { window.__forgeRig.app.page = 'firmware' }, 0); 1"); time.sleep(10)
    r.shot("t09-firmware", "T09: Firmware-Seite vor dem Update", page="firmware", cdp=cdp)

    def run_update(source_js, label):
        t0 = time.time()
        res = cdp.js(source_js, timeout_ms=600000)
        r.say(f"   {label}: Quelle geladen ({res})")
        cdp.js("""(() => { for (const l of document.querySelectorAll('label.check input[type=checkbox]')) {
                    const txt = l.parentElement.textContent; if (/zuerst|first/i.test(txt) && l.checked) l.click();
                    if (/gelesen|read/i.test(txt) && !l.checked) l.click(); }
                  const b = [...document.querySelectorAll('button.primary')].find(x => !x.disabled); if (b) b.click(); return !!b; })()""")
        done = None
        while time.time() - t0 < 900:
            time.sleep(10)
            try:
                txt = cdp.js("document.querySelector('main') ? document.querySelector('main').innerText.slice(0, 4000) : document.body.innerText.slice(0, 4000)")
            except Exception:
                txt = ""
            if re.search(r"Bytes geschrieben|bytes written", txt or ""):
                done = True; break
            if re.search(r"Fehler|failed|abgebrochen|cancelled", txt or ""):
                done = False; r.say("   " + (txt or "")[:300].replace("\n", " ")); break
        return done, time.time() - t0

    ok, dur = run_update(r"""(async () => { const sel = document.querySelectorAll('select');
        if (sel.length < 2) return 'keine Releaseauswahl';
        const tag = [...sel[0].options].find(o => /2\.6\.1/.test(o.textContent)); if (tag) { sel[0].value = tag.value; sel[0].dispatchEvent(new Event('change')); }
        await new Promise(r => setTimeout(r, 1500));
        const f = [...sel[1].options].find(o => /96ELN-2\.6\.1\.hex/.test(o.textContent)); if (f) { sel[1].value = f.value; sel[1].dispatchEvent(new Event('change')); }
        const b = [...document.querySelectorAll('button')].find(x => /^(Laden|Load)$/.test(x.textContent.trim())); b && b.click();
        await new Promise(r => setTimeout(r, 15000)); return (tag ? tag.textContent : '?') + ' / ' + (f ? f.textContent : '?'); })()""", "Release")
    r.check("T09", "Update aus dem Release 2.6.1 über die App", ok, f"{dur:.0f} s")
    t = rig.wait_online(f, 180)
    ver = f.get("system.firmware.port")[0].get("system.firmware.port")
    r.check("T09", "Forge läuft danach mit 2.6.1", ver == '"2.6.1"', f"{ver}, wieder verbunden nach {t and round(t)} s")
    after = {k: f.get(k)[0].get(k) for k in probe}
    r.check("T09", "Abstimmung nach dem Update erhalten", after == probe, f"{[k for k in probe if after[k] != probe[k]]}")
    if local_hex:
        text = open(local_hex).read()
        js = ("(async () => { const inp = document.querySelector('input[type=file]'); const dt = new DataTransfer(); "
              f"dt.items.add(new File([{json.dumps(text)}], {json.dumps(os.path.basename(local_hex))}, {{type: 'text/plain'}})); inp.files = dt.files; "
              "inp.dispatchEvent(new Event('change', {bubbles: true})); await new Promise(r => setTimeout(r, 4000)); return inp.files[0].name; })()")
        cdp2 = rig.open_gui(); cdp2.js("window.confirm = () => true; setTimeout(() => { window.__forgeRig.app.page = 'firmware' }, 0); 1"); time.sleep(10)
        cdp_saved = cdp
        cdp = cdp2
        ok, dur = run_update(js, "Datei")
        r.check("T09", f"Update aus einer Datei über die App ({os.path.basename(local_hex)})", ok, f"{dur:.0f} s")
        t = rig.wait_online(f, 180)
        ver = f.get("system.firmware.port")[0].get("system.firmware.port")
        r.check("T09", "Forge läuft danach mit dem Dateistand", ver and ver.strip('"') in os.path.basename(local_hex), f"{ver}")
        after = {k: f.get(k)[0].get(k) for k in probe}
        r.check("T09", "Abstimmung auch danach erhalten", after == probe, f"{[k for k in probe if after[k] != probe[k]]}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tests", nargs="*", default=["T01", "T02", "T03", "T11", "T04", "T05", "T06", "T07", "T08"])
    ap.add_argument("--apk", help="install this debug APK first")
    ap.add_argument("--hex", help="T09: local firmware file for the second update")
    ap.add_argument("--cycles", type=int, default=30)
    ap.add_argument("--requests", type=int, default=300)
    ap.add_argument("--soak", type=int, default=0, help="T10 minutes")
    a = ap.parse_args()
    if a.apk:
        rig.install(a.apk)
        # a fresh install has no USB permission and missed the attach: plug in again, the dialog follows
        rig.unplug(); time.sleep(3); rig.plug(); rig.accept_usb_dialog(wait=25)
    r = Run(); f = rig.Forge()
    r.say(f"Ziel {rig.SERIAL}: {rig.sh('getprop ro.product.name').strip()} {rig.sh('getprop ro.lineage.version').strip()}, "
          f"App {rig.sh(f'dumpsys package {rig.PKG} | grep -m1 versionName').strip()}")
    rig.wake(); rig.plug(); rig.accept_usb_dialog(wait=5)
    if rig.wait_online(f, 15) is None:   # no permission yet (fresh install) or attach missed: plug in again
        rig.unplug(); time.sleep(3); rig.plug(); rig.accept_usb_dialog(wait=25)
    r.check("Start", "Forge über die App erreichbar", rig.wait_online(f, 90) is not None)
    fw = f.get("system.firmware.port")[0].get("system.firmware.port"); r.say(f"Forge-Firmware {fw}")
    cdp = None
    try:
        for t in a.tests:
            if t in ("T04", "T05", "T06", "T09", "T10", "T11", "T12", "T13", "T14", "T15") and cdp is None:
                cdp = rig.open_gui()
                t_read = time.time(); n = 0
                while time.time() - t_read < 300:
                    try:
                        # read through: online and no reading in progress (the port firmware lists only channels
                        # with filters in use, so their number depends on the tuning)
                        n = cdp.js("window.__forgeRig.app.status === 'online' && !window.__forgeRig.app.progress ? Object.keys(window.__forgeRig.app.device.biquads).length : 0", timeout_ms=20000) or 0
                    except Exception:
                        n = 0; cdp = rig.open_gui()
                    if n >= 1:
                        break
                    time.sleep(5)
                r.check(t, "GUI hat den Forge eingelesen (online, Einlesen beendet)", n >= 1, f"{n} Kanäle mit Filtern, {time.time() - t_read:.0f} s")
                r.data.setdefault("gui_read_s", []).append(round(time.time() - t_read))
            t_start = time.time()
            {"T12": lambda: t12(r, f, cdp), "T13": lambda: t13(r, f, cdp), "T14": lambda: t14(r, f, cdp), "T15": lambda: t15(r, f, cdp),
             "T09": lambda: t09(r, f, cdp, a.hex),
             "T11": lambda: t11(r, f, cdp), "T01": lambda: t01(r, f), "T02": lambda: t02(r, f, a.cycles), "T03": lambda: t03(r, f, a.requests),
             "T04": lambda: t04(r, f, cdp), "T05": lambda: t05(r, f, cdp), "T06": lambda: t06(r, f, cdp),
             "T07": lambda: t07(r, f), "T08": lambda: t08(r, f)}[t]()
            r.window(t, t_start, time.time(), expect_drop=t in ("T04", "T06", "T08", "T09", "T11", "T12"))
            r.save()   # interim report after every test (make_report.py reads it)
            pages = {"T01": [("overview", "Übersicht nach dem Verbinden")], "T02": [("overview", "Übersicht nach dem letzten Wiederverbinden")],
                     "T04": [("settings", "Einstellungen: Android-Lautstärke"), ("car", "Fahrzeug-Seite mit Master")],
                     "T05": [("mixer", "Mixer nach dem Gleichzeitigkeitstest")], "T06": [("modc", "Steuerblöcke nach dem Neustart")],
                     "T07": [("overview", "Übersicht nach Prozessende und Doze")], "T08": [("overview", "Übersicht nach dem Forge-Neustart")],
                     "T11": [("filters", "Filterseite am Ende")]}
            if pages.get(t):
                try:
                    c2 = cdp or rig.open_gui()
                    for pg, cap in pages[t]:
                        r.shot(f"{t.lower()}-{pg}", f"{t}: {cap}", page=pg, cdp=c2)
                    cdp = c2
                except Exception as ex:
                    r.say(f"   (Screenshot {t}: {ex})")
            if t in ("T01", "T06", "T07"):
                cdp = None   # the app was restarted
        if a.soak:
            cdp = cdp or rig.open_gui(); t10(r, f, cdp, a.soak)
    finally:
        r.save()


if __name__ == "__main__":
    main()
