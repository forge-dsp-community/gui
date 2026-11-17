#!/usr/bin/env python3
"""Watches the Forge's audio during the tests (#33): a steady tone from the PC through the Forge's USB
audio, outputs 1/2 at the PC line-in; every 200 ms the tone level per channel and the energy beside it.
Writes events (dropout, level step, click, back) with time stamps, and a level line every 10 s.

    audio_monitor.py [--freq 7100] [--db -20] [--log results/audio-<time>.log]

Relative measurement (PipeWire line-in incl. its software gain): the reference is the median of the
first 5 s."""
import argparse, os, subprocess, sys, threading, time, wave
import numpy as np

SINK = "alsa_output.usb-Forge_DSP_Forge_DSP_96ELN_-_Audio_2U2971090C000100-00.analog-stereo"
SRC = "alsa_input.pci-0000_0e_00.4.analog-stereo"
FS = 48000
BLOCK = FS // 5   # 200 ms


def tone_file(path, f, db, seconds=300):
    if os.path.exists(path):
        return path
    t = np.arange(FS * seconds) / FS
    x = np.sin(2 * np.pi * f * t) * 10 ** (db / 20)
    pcm = (np.stack([x, x], 1) * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(FS); w.writeframes(pcm.tobytes())
    return path


def player(path, stop):
    while not stop.is_set():   # the file lasts 5 min; a gap of a few ms between loops is marked
        p = subprocess.Popen(["pw-play", "--target", SINK, path])
        while p.poll() is None and not stop.is_set():
            time.sleep(0.2)
        if p.poll() is None:
            p.terminate()
        LOOPS.append(time.time())


LOOPS = []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--freq", type=float, default=7100)
    ap.add_argument("--db", type=float, default=-6)
    ap.add_argument("--log", default=None)
    ap.add_argument("--csv", default=None, help="levels every block: time,L,R,floorL,floorR,besideL,besideR (dB)")
    a = ap.parse_args()
    here = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(os.path.join(here, "results"), exist_ok=True)
    log = open(a.log or os.path.join(here, "results", time.strftime("audio-%Y%m%d-%H%M%S.log")), "a", buffering=1)

    def out(msg):
        log.write(time.strftime("%H:%M:%S.") + f"{int(time.time() * 1000) % 1000:03d} {msg}\n")

    csv = open(a.csv, "a", buffering=1) if a.csv else None
    path = tone_file(os.path.join(here, "results", f"monitor_{int(a.freq)}.wav"), a.freq, a.db)
    stop = threading.Event()
    threading.Thread(target=player, args=(path, stop), daemon=True).start()
    # straight from the hardware: the PipeWire path of this line-in carries broadband noise at about
    # -1 dBFS that arecord on hw:1,0 does not show (floor about -105 dBFS); PipeWire lets go after a suspend
    subprocess.run(["pactl", "suspend-source", SRC, "1"], capture_output=True)
    rec = None
    for _ in range(30):
        rec = subprocess.Popen(["arecord", "-q", "-D", "hw:1,0", "-f", "S16_LE", "-r", str(FS), "-c", "2", "-t", "raw"],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        time.sleep(0.5)
        if rec.poll() is None:
            break
    if rec.poll() is not None:
        out("Line-In nicht frei (PipeWire hält hw:1,0)"); stop.set(); return
    win = np.hanning(BLOCK); fr = np.fft.rfftfreq(BLOCK, 1 / FS)
    tone = np.abs(fr - a.freq) < 12
    near = (np.abs(fr - a.freq) > 100) & (np.abs(fr - a.freq) < 400)   # noise right beside the tone
    # beside the tone: 200 Hz..20 kHz without the tone and its harmonics (distortion is no click)
    beside = (fr > 200) & (fr < 20000)
    for k in (1, 2, 3):
        beside &= np.abs(fr - k * a.freq) > 300
    ref, hist, state, last_line = None, [], ["ok", "ok"], time.time()
    pend = [None, None]   # a new state counts after two blocks in a row
    # the line-in at 0 dB hardware gain (forge_measure --gain 30 leaves +30 dB, which clips this tone)
    subprocess.run(["amixer", "-c", "1", "sset", "Capture,0", "23"], capture_output=True)
    subprocess.run(["amixer", "-c", "1", "sset", "Line Boost,0", "0"], capture_output=True)
    subprocess.run(["pactl", "set-source-volume", SRC, "100%"], capture_output=True)
    out(f"start: Ton {a.freq:.0f} Hz {a.db:.0f} dBFS, Ausgang 1 = L, 2 = R")
    try:
        while True:
            b = rec.stdout.read(BLOCK * 4)
            if len(b) < BLOCK * 4:
                out("Aufnahme beendet"); break
            y = np.frombuffer(b, "<i2").reshape(-1, 2) / 32768.0
            lv, bs, fl = [], [], []
            for c in range(2):
                S = np.abs(np.fft.rfft(y[:, c] * win)) / (win.sum() / 2)
                lv.append(20 * np.log10(np.sqrt((S[tone] ** 2).sum()) + 1e-9))
                fl.append(20 * np.log10(np.sqrt((S[near] ** 2).mean() * tone.sum()) + 1e-9))
                bs.append(20 * np.log10(np.sqrt((S[beside] ** 2).sum()) + 1e-9))
            now = time.time()
            if csv:
                csv.write(f"{now:.2f},{lv[0]:.2f},{lv[1]:.2f},{fl[0]:.2f},{fl[1]:.2f},{bs[0]:.2f},{bs[1]:.2f}\n")
            if ref is None:
                hist.append((lv, bs))
                if len(hist) >= 25:
                    ref = (np.median([h[0] for h in hist], 0), np.median([h[1] for h in hist], 0))
                    out(f"Referenz: Ton L/R {ref[0][0]:.1f}/{ref[0][1]:.1f} dB, daneben {ref[1][0]:.1f}/{ref[1][1]:.1f} dB")
                continue
            for c, name in enumerate("LR"):
                d = lv[c] - ref[0][c]; n = bs[c] - ref[1][c]
                new = "aus" if lv[c] < fl[c] + 6 else "leiser" if d < -6 else "lauter" if d > 6 else "ok"
                if new != state[c]:
                    if pend[c] == new:
                        out(f"{name}: {state[c]} -> {new} (Ton {d:+.1f} dB)"); state[c] = new; pend[c] = None
                    else:
                        pend[c] = new
                else:
                    pend[c] = None
                if n > 15 and new == "ok":
                    out(f"{name}: Störung/Knackser neben dem Ton {n:+.1f} dB")
            if now - last_line >= 10:
                # PipeWire may switch the card's capture (Front Mic, +30 dB) when USB devices come and go
                ins = subprocess.run(["amixer", "-c", "1", "sget", "Input Source,0"], capture_output=True, text=True).stdout
                if "Item0: 'Line'" not in ins:
                    out("Line-In war umgestellt (" + ins.strip().splitlines()[-1].strip() + "), zurück auf Line, 0 dB")
                    subprocess.run(["amixer", "-c", "1", "sset", "Input Source,0", "Line"], capture_output=True)
                    subprocess.run(["amixer", "-c", "1", "sset", "Capture,0", "23"], capture_output=True)
                out(f"Pegel L/R {lv[0] - ref[0][0]:+.1f}/{lv[1] - ref[0][1]:+.1f} dB, Abstand zum Rauschen "
                    f"{lv[0] - fl[0]:.0f}/{lv[1] - fl[1]:.0f} dB, daneben "
                    f"{bs[0] - ref[1][0]:+.1f}/{bs[1] - ref[1][1]:+.1f} dB")
                last_line = now
            while LOOPS:
                out(f"Wiedergabe-Schleife neu gestartet (Lücke erwartet) {LOOPS.pop():.0f}")
    except KeyboardInterrupt:
        pass
    finally:
        stop.set(); rec.terminate()
        subprocess.run(["pactl", "suspend-source", SRC, "0"], capture_output=True)


if __name__ == "__main__":
    main()
