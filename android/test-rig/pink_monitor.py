#!/usr/bin/env python3
"""Pink noise through the Forge's USB audio, outputs 1/2 at the PC line-in (arecord hw:1,0, PipeWire source
suspended): every second a third-octave spectrum per channel. Writes
  --csv  time, broadband L, R (dBFS), then 31 bands L and 31 bands R (dB), 25 Hz .. 20 kHz
  --log  events: broadband drop > 6 dB (dropout), back, level line every 10 s
Spectra of two moments divided give |H|^2 of whatever changed in between (no sync needed)."""
import argparse, os, subprocess, threading, time, wave
import numpy as np

SINK = "alsa_output.usb-Forge_DSP_Forge_DSP_96ELN_-_Audio_2U2971090C000100-00.analog-stereo"
SRC = "alsa_input.pci-0000_0e_00.4.analog-stereo"
FS = 48000
BANDS = [25 * 2 ** (i / 3) for i in range(31)]   # 25 Hz .. 20 kHz


def pink_file(path, db, seconds=300):
    if os.path.exists(path):
        return path
    rnd = np.random.default_rng(1); n = FS * seconds
    X = np.fft.rfft(rnd.standard_normal(n)); f = np.fft.rfftfreq(n, 1 / FS)
    X[(f < 20) | (f > 20000)] = 0; f[0] = 1; X /= np.sqrt(f)
    x = np.fft.irfft(X, n); x = x / x.std() * 10 ** (db / 20)
    pcm = (np.clip(np.stack([x, x], 1), -1, 1) * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(FS); w.writeframes(pcm.tobytes())
    return path


def periodic_file(path, db, period, seconds=300):
    """one period of pink noise (flat magnitude per 1/f, random phase), repeated; the period itself is
    stored beside it (.npy) as the reference X(f) of the analysis"""
    if os.path.exists(path):
        return path
    rnd = np.random.default_rng(2); n = period
    f = np.fft.rfftfreq(n, 1 / FS); mag = np.zeros(len(f)); band = (f >= 20) & (f <= 20000)
    mag[band] = 1 / np.sqrt(f[band])
    X = mag * np.exp(2j * np.pi * rnd.random(len(f)))
    x = np.fft.irfft(X, n); x = x / x.std() * 10 ** (db / 20)
    np.save(path[:-4] + ".npy", x)
    reps = int(seconds * FS / n) + 1
    y = np.tile(x, reps)
    pcm = (np.clip(np.stack([y, y], 1), -1, 1) * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(FS); w.writeframes(pcm.tobytes())
    return path


def player(path, stop):
    while not stop.is_set():
        p = subprocess.Popen(["pw-play", "--target", SINK, path])
        while p.poll() is None and not stop.is_set():
            time.sleep(0.2)
        if p.poll() is None:
            p.terminate()


SRC_VOLUME = "46%"   # PipeWire volume is cubic: 46 % = -20 dB below 100 %; pink at about -32 dBFS RMS, peaks clear of 0 dBFS


def setup_line_in():
    """PipeWire sets the codec's pins and gain for the line-in port only while it runs the source; the analog
    level of the Forge at the PC's line-in is low, so the gain comes from PipeWire (100 % is about +57 dB over
    0 dB hardware gain; that clips pink noise at the line-in, which fills notches and stop bands: SRC_VOLUME). Then the source is suspended: arecord needs hw:1,0 alone. Always the same, so the
    level does not jump between runs."""
    subprocess.run(["pactl", "suspend-source", SRC, "0"], capture_output=True)
    subprocess.run(["pactl", "set-source-port", SRC, "analog-input-linein"], capture_output=True)
    subprocess.run(["pactl", "set-source-volume", SRC, SRC_VOLUME], capture_output=True)
    time.sleep(1.5)
    subprocess.run(["pactl", "suspend-source", SRC, "1"], capture_output=True)
    time.sleep(0.5)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=float, default=-18, help="pink noise RMS, dBFS")
    ap.add_argument("--csv", required=True); ap.add_argument("--log", required=True)
    ap.add_argument("--file", help="pink noise file to play instead of the generated one")
    ap.add_argument("--periodic", type=int, default=0, help="periodic pink noise, period in samples (e.g. 65536); "
                    "a transfer function with phase from any period of the recording (phase, delay, polarity)")
    ap.add_argument("--raw", help="directory for the raw recording (5-min files, the last 3 kept)")
    a = ap.parse_args()
    here = os.path.dirname(os.path.abspath(__file__))
    log = open(a.log, "a", buffering=1); csv = open(a.csv, "a", buffering=1)
    out = lambda m: log.write(time.strftime("%H:%M:%S ") + m + "\n")
    if a.periodic:
        path = periodic_file(os.path.join(here, "results", f"monitor_pink_p{a.periodic}.wav"), a.db, a.periodic)
    else:
        path = a.file or pink_file(os.path.join(here, "results", "monitor_pink.wav"), a.db)
    stop = threading.Event(); threading.Thread(target=player, args=(path, stop), daemon=True).start()
    setup_line_in()
    rec = None
    for _ in range(30):
        rec = subprocess.Popen(["arecord", "-q", "-D", "hw:1,0", "-f", "S16_LE", "-r", str(FS), "-c", "2", "-t", "raw"],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        time.sleep(0.5)
        if rec.poll() is None:
            break
    if rec.poll() is not None:
        out("Line-In nicht frei"); return
    n = FS; win = np.hanning(n); fr = np.fft.rfftfreq(n, 1 / FS)
    masks = [(fr >= c / 2 ** (1 / 6)) & (fr < c * 2 ** (1 / 6)) for c in BANDS]
    raw_f, raw_t0, raw_idx = None, 0, None
    if a.raw:
        os.makedirs(a.raw, exist_ok=True); raw_idx = open(os.path.join(a.raw, "index.csv"), "a", buffering=1)
    out(f"start: rosa Rauschen {a.db:.0f} dBFS, Terzspektrum je Sekunde, Ausgang 1 = L, 2 = R")
    ref = None; state = ["ok", "ok"]; last = time.time()
    try:
        while True:
            b = rec.stdout.read(n * 4)
            if len(b) < n * 4:
                out("Aufnahme beendet"); break
            y = np.frombuffer(b, "<i2").reshape(-1, 2) / 32768.0; now = time.time()
            if raw_idx:
                if raw_f is None or now - raw_t0 > 300:
                    if raw_f:
                        raw_f.close()
                    name = time.strftime("raw-%H%M%S.s16"); raw_f = open(os.path.join(a.raw, name), "ab"); raw_t0 = now
                    old = sorted(x for x in os.listdir(a.raw) if x.startswith("raw-"))[:-3]
                    for x in old:
                        os.remove(os.path.join(a.raw, x))
                raw_idx.write(f"{now - n / FS:.3f},{name},{raw_f.tell()}\n"); raw_f.write(b)
            row = [now]; bands = []
            for c in range(2):
                row.append(20 * np.log10(y[:, c].std() + 1e-9))
                P = np.abs(np.fft.rfft((y[:, c] - y[:, c].mean()) * win)) ** 2
                bands.append([10 * np.log10(P[m].sum() + 1e-12) for m in masks])
            row += bands[0] + bands[1]
            csv.write(",".join(f"{v:.2f}" for v in row) + "\n")
            if ref is None:
                ref = row[1:3]; out(f"Referenz breitbandig L/R {ref[0]:.1f}/{ref[1]:.1f} dBFS"); continue
            for c, name in enumerate("LR"):
                d = row[1 + c] - ref[c]
                new = "aus" if d < -30 else "leiser" if d < -6 else "ok"
                if new != state[c]:
                    out(f"{name}: {state[c]} -> {new} ({d:+.1f} dB breitbandig)"); state[c] = new
            if now - last >= 10:
                last = now
                out(f"Pegel L/R {row[1] - ref[0]:+.1f}/{row[2] - ref[1]:+.1f} dB")
                ins = subprocess.run(["amixer", "-c", "1", "sget", "Input Source,0"], capture_output=True, text=True).stdout
                if "Item0: 'Line'" not in ins:
                    out("Line-In war umgestellt, neu eingerichtet"); setup_line_in()
    finally:
        stop.set(); rec.terminate()
        subprocess.run(["pactl", "suspend-source", SRC, "0"], capture_output=True)


if __name__ == "__main__":
    main()
