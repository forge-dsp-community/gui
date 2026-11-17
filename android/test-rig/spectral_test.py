#!/usr/bin/env python3
"""Pink noise spectrum test of filters and EQ bands (CP2102 at the PC, pink_monitor.py running).
Output 1 (L) gets the changes, output 2 (R) stays as the reference. Each step: third-octave spectrum
before and after (4 s each), the difference against the firmware formulas (tests/test_cmd.py bq_reference).
The crossover filters of outputs 1/2 are bypassed for the test (read back) and restored at the end.

    spectral_test.py results/<dir>     writes spectral.json and spectral.html (one chart per step)"""
import json, os, re, statistics, sys, time
import numpy as np
import serial

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.expanduser("~/avinavant/work/forge-firmware/tests"))
os.environ.setdefault("NATIVE_DIR", "/nonexistent")
import test_cmd as T

CSV = os.path.join(HERE, "results", "pink-spectra.csv")
BANDS = [25 * 2 ** (i / 3) for i in range(31)]
FLAV = "V40-96ELN"
s = serial.Serial("/dev/ttyUSB0", 38400, timeout=0.05)


def q(c, t=1.5):
    s.reset_input_buffer(); s.write((c + "\r\n").encode()); out = b""; e = time.time() + t
    while time.time() < e:
        d = s.read(8192); out += d
        if d: e = max(e, time.time() + 0.25)
    return out.decode(errors="replace")


def get(reg):
    m = re.search(r"<\.1 " + re.escape(reg) + r"=(\S*)", q("get.1 " + reg)); return m.group(1) if m else None


def put(reg, val):
    r = reg[:-5] if reg.endswith(".calc") else reg
    for _ in range(3):
        q(f"set.1 {reg}={val}")
        got = get(r)
        # an empty place reads back normalised ([0,10,0,1,0,0]); only its type counts
        if got == val or (val.startswith("[0,") and got and got.startswith("[0,")):
            return True
    raise SystemExit(f"read back failed: {reg}={val} ({get(r)})")


def spectrum(t0, t1):
    rows = []
    with open(CSV) as f:
        for line in f:
            v = line.split(",")
            if len(v) == 65 and t0 <= float(v[0]) <= t1:
                rows.append([float(x) for x in v])
    if not rows:
        return None
    a = np.array(rows)
    # power average of the blocks per band
    L = 10 * np.log10(np.mean(10 ** (a[:, 3:34] / 10), 0)); R = 10 * np.log10(np.mean(10 ** (a[:, 34:65] / 10), 0))
    return L, R, a[:, 1:3].mean(0)


def measure(seconds=12.0):
    t0 = time.time(); time.sleep(seconds + 1.2)
    return spectrum(t0 + 0.6, time.time())


def model(params):
    out = []
    for c in BANDS:
        fr = np.geomspace(c / 2 ** (1 / 6), c * 2 ** (1 / 6), 9)
        h = np.ones(len(fr), complex)
        for p in params:
            raw = " ".join(f"{v:08X}" for v in T.bq_reference(FLAV, *p))
            h *= np.array([T.bq_response(raw, f) for f in fr])
        out.append(10 * np.log10(np.mean(np.abs(h) ** 2)))
    return np.array(out)


def vu_inputs():
    q("get.1 system.vu"); time.sleep(1.3)
    v = get("system.vu"); return [int(x) for x in v.strip("[]").split(",")] if v else []


# steps on output 1, slot 10 (free after the bypass); type, f, gain 0.1 dB, q/1000, b, flags
STEPS = [
    ("EQ 1 kHz +6 dB, Q 1,4", [(1, 1000, 60, 1414, 0, 128)]),
    ("EQ 1 kHz −9 dB, Q 4", [(1, 1000, -90, 4000, 0, 128)]),
    ("EQ 120 Hz +6 dB, Q 0,7", [(1, 120, 60, 707, 0, 128)]),
    ("EQ 6,3 kHz −6 dB, Q 2", [(1, 6300, -60, 2000, 0, 128)]),
    ("Bass-Shelf 100 Hz +6 dB", [(14, 100, 60, 707, 0, 128)]),
    ("Höhen-Shelf 8 kHz −6 dB", [(15, 8000, -60, 707, 0, 128)]),
    ("Hochpass 300 Hz Butterworth 12 dB/Okt", [(9, 300, 0, 707, 0, 128)]),
    ("Hochpass 300 Hz LR4 (2× Butterworth)", [(9, 300, 0, 707, 0, 128), (9, 300, 0, 707, 0, 128)]),
    ("Tiefpass 2 kHz LR4", [(3, 2000, 0, 707, 0, 128), (3, 2000, 0, 707, 0, 128)]),
    ("Bandpass 300 Hz–2 kHz (HP + TP, je LR4)", [(9, 300, 0, 707, 0, 128), (9, 300, 0, 707, 0, 128), (3, 2000, 0, 707, 0, 128), (3, 2000, 0, 707, 0, 128)]),
    ("Allpass 1 kHz (Betrag unverändert)", [(21, 1000, 0, 707, 0, 128)]),
    ("EQ 1 kHz +6 dB, ausgeschaltet (Flag aus)", [(1, 1000, 60, 1414, 0, 0)]),
]
SLOTS = [10, 11, 12, 13]


def chart(name, bands, meas_l, meas_r, mod, ok_mask):
    W, H, L, B, Tp = 680, 220, 44, 30, 10
    lo, hi = -40.0, 12.0
    X = lambda f: L + (np.log10(f) - np.log10(25)) / (np.log10(20000) - np.log10(25)) * (W - L - 8)
    Y = lambda v: Tp + (hi - max(lo, min(hi, v))) / (hi - lo) * (H - Tp - B)
    g = []
    for v in (-40, -30, -20, -10, 0, 10):
        g.append(f"<line x1='{L}' x2='{W - 8}' y1='{Y(v):.1f}' y2='{Y(v):.1f}' class='grid'/><text x='{L - 6}' y='{Y(v) + 4:.1f}' class='ax' text-anchor='end'>{v:+d}</text>")
    for f, lab in ((31.5, "31"), (100, "100"), (315, "315"), (1000, "1k"), (3150, "3,15k"), (10000, "10k")):
        g.append(f"<line x1='{X(f):.1f}' x2='{X(f):.1f}' y1='{Tp}' y2='{H - B}' class='grid'/><text x='{X(f):.1f}' y='{H - B + 15}' class='ax' text-anchor='middle'>{lab}</text>")
    pm = " ".join(f"{'M' if i == 0 else 'L'}{X(f):.1f},{Y(v):.1f}" for i, (f, v) in enumerate(zip(bands, mod)))
    pr = " ".join(f"{'M' if i == 0 else 'L'}{X(f):.1f},{Y(v):.1f}" for i, (f, v) in enumerate(zip(bands, meas_r)))
    dots = "".join(f"<circle cx='{X(f):.1f}' cy='{Y(v):.1f}' r='2.6' class='{'pt' if ok else 'pt off'}'/>" for f, v, ok in zip(bands, meas_l, ok_mask))
    return (f"<svg viewBox='0 0 {W} {H}' role='img' aria-label='{name}'>{''.join(g)}<path d='{pr}' class='ref'/><path d='{pm}' class='model'/>{dots}"
            f"<text x='{L - 34}' y='{Tp + 4}' class='ax'>dB</text><text x='{W - 8}' y='{H - 4}' class='ax' text-anchor='end'>Hz</text></svg>")


FLOOR = None


def find_route():
    """each output alone: the one with the strongest pink noise at the line-in and that channel (0 = L, 1 = R)"""
    for o in range(1, 9):
        put(f"mute.out.{o}", "1")
    time.sleep(3); t = time.time(); time.sleep(8); fs = spectrum(t, time.time()); floor = fs[2]
    global FLOOR
    FLOOR = fs
    best = (None, None, -1e9)
    for o in range(1, 9):
        put(f"mute.out.{o}", "0"); time.sleep(3); t = time.time(); time.sleep(5)
        lv = spectrum(t, time.time())[2]; put(f"mute.out.{o}", "1")
        for c in (0, 1):
            if lv[c] - floor[c] > best[2]:
                best = (o, c, lv[c] - floor[c])
    print(f"Weg: Ausgang {best[0]} -> Line-In {'LR'[best[1]]} ({best[2]:.0f} dB über dem Rauschen)", flush=True)
    return best


def main(outdir):
    os.makedirs(outdir, exist_ok=True)
    assert spectrum(time.time() - 3, time.time()) is not None, "pink_monitor.py is not running"
    # bypass the crossover/EQ of outputs 1 and 2 (remember, read back), keep the slots for the steps free
    mutes = {o: get(f"mute.out.{o}") for o in range(1, 9)}
    OUT, CH, snr = find_route()
    if snr < 6:
        raise SystemExit("no output reaches the line-in")
    saved = {}
    for ch in (OUT,):
        for n in range(1, 14):
            v = get(f"biquad.{ch}.{n}")
            saved[(ch, n)] = v
            t, f, gg, qq, b, fl = map(int, v.strip("[]").split(","))
            if t and fl & 0x80:
                put(f"biquad.{ch}.{n}.calc", f"[{t},{f},{gg},{qq},{b},{fl & 0x7F}]")
    for n in SLOTS:
        put(f"biquad.{OUT}.{n}.calc", "[0,0,0,0,0,0]")
    # only the measured output plays (the others are muted, read back; restored at the end)
    put(f"mute.out.{OUT}", "0")
    time.sleep(3)
    res = []
    try:
        for name, flt in STEPS:
            g = vu_inputs()
            a = measure()
            for n, p in zip(SLOTS, flt):
                put(f"biquad.{OUT}.{n}.calc", "[" + ",".join(map(str, p)) + "]")
            time.sleep(2.5)   # clickless fade (#44)
            b = measure()
            for n in SLOTS[:len(flt)]:
                put(f"biquad.{OUT}.{n}.calc", "[0,0,0,0,0,0]")
            time.sleep(2.5)
            dl, dr = b[CH] - a[CH], b[1 - CH] - a[1 - CH]
            mod = model(flt)
            # bands from 40 Hz (the narrow low bands hold too few lines), where the target is above -30 dB and the
            # level before the change is well above the noise of the line-in
            # bands well above the line-in's own noise in that band (measured with all outputs muted)
            snr = a[CH] - FLOOR[CH]
            # a band counts where the expected change stays 6 dB inside the band's distance to the line-in noise
            usable = (np.array(BANDS) >= 40) & (snr > 15) & (mod > -(snr - 6))
            # the level of the noise drifts between the two windows: remove the offset of the flat bands
            flat = usable & (np.abs(mod) < 0.5)
            offset = float(np.median((dl - mod)[flat])) if flat.sum() >= 3 else 0.0
            dl = dl - offset
            err = np.abs(dl - mod)[usable]
            worst = float(err.max()) if err.size else float("nan")
            refdev = float(np.abs(dr[np.array(BANDS) >= 40]).max())
            r = {"out": OUT, "chan": "LR"[CH], "step": name, "bands": BANDS, "dL": dl.tolist(), "dR": dr.tolist(), "model": mod.tolist(),
                 "usable": usable.tolist(), "worst": worst, "refdev": refdev, "inputs": g,
                 "offset": offset, "a": a[CH].tolist(), "b": b[CH].tolist(), "floor": FLOOR[CH].tolist(),
                 "ok": bool(worst <= 2.0)}
            res.append(r)
            print(f"{name:45s} max. Abweichung {worst:4.1f} dB, Bezug R {refdev:3.1f} dB, Eingänge G7/G8 {g[6:8] if len(g) == 8 else g}", flush=True)
            if len(g) == 8 and (g[6] < 150 or g[7] < 150):
                print("   AUSFALL der Digitaleingänge", flush=True)
    finally:
        for o, v in mutes.items():
            if v is not None:
                put(f"mute.out.{o}", v)
        for (ch, n), v in saved.items():
            if v:
                put(f"biquad.{ch}.{n}.calc", v)
        print("Filter von Ausgang 1/2 wiederhergestellt (zurückgelesen)", flush=True)
    json.dump(res, open(os.path.join(outdir, "spectral.json"), "w"))
    parts = []
    for r in res:
        parts.append(f"<figure class='spec'><figcaption><strong>{r['step']}</strong> · "
                     f"<span class='{'okt' if r['ok'] else 'badt'}'>max. Abweichung {r['worst']:.1f} dB</span></figcaption>"
                     + chart(r["step"], r["bands"], r["dL"], r["dR"], r["model"], r["usable"]) + "</figure>")
    open(os.path.join(outdir, "spectral.html"), "w").write(
        "<p class='muted'>Rosa Rauschen (ETON, −12 dBFS) über USB-Audio, Terzspektrum am Line-In. Punkte: gemessene Änderung am "
        f"Ausgang {res[0]['out'] if res else '?'} (alle anderen während des Tests stumm); Linie: Soll aus den Formeln der Firmware; gestrichelt: der andere Kanal des Line-In (ohne Signal, zur Kontrolle). Blasse Punkte: zu wenig "
        "Pegel für eine Aussage. Weichenfilter von Ausgang 1/2 während des Tests überbrückt.</p><div class='specs'>" + "".join(parts) + "</div>")
    ok = sum(r["ok"] for r in res)
    print(f"{ok}/{len(res)} Schritte innerhalb ±2 dB", flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
