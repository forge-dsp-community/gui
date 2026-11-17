"""Transfer function with phase from the monitor's raw recording of periodic pink noise (#33).
The source is one period x (results/monitor_pink_p<N>.npy) repeated; any N consecutive samples of the
recording are a circular shift of x through the system, so FFT(rec)/FFT(x) = H(f) * exp(-j w tau).
tau (latency, sub-sample) comes from the circular cross-correlation; comparing two windows removes it."""
import glob, os
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
FS = 48000
RAW = os.path.join(HERE, "results", "raw")


def period():
    f = sorted(glob.glob(os.path.join(HERE, "results", "monitor_pink_p*.npy")))
    if not f:
        return None
    return np.load(f[-1])


def recording(t0, t1):
    """samples (n, 2) recorded between t0 and t1 (monitor --raw)"""
    idx = []
    with open(os.path.join(RAW, "index.csv")) as f:
        for line in f:
            v = line.strip().split(",")
            if len(v) == 4:
                idx.append((float(v[0]), v[1], int(v[2]), int(v[3])))
    parts, start = [], None
    for t, name, off, tot in idx:
        if t >= t0 and t + 1.0 <= t1:
            p = os.path.join(RAW, name)
            if not os.path.exists(p):
                continue
            with open(p, "rb") as fh:
                fh.seek(off); b = fh.read(FS * 4)
            if len(b) == FS * 4:
                if start is None:
                    start = tot
                elif tot != start + sum(len(x) for x in parts):
                    break   # not contiguous
                parts.append(np.frombuffer(b, "<i2").reshape(-1, 2) / 32768.0)
    return (np.concatenate(parts), start) if parts else None


def measure(t0, t1, ch):
    """(f, H complex with latency, latency in samples) of channel ch from the window"""
    x = period(); n = len(x)
    rec = recording(t0, t1)
    if rec is None or len(rec[0]) < 2 * n:
        return None
    y, s0 = rec
    k = len(y) // n
    seg = y[:k * n, ch].reshape(k, n)
    X = np.fft.rfft(x)
    Y = np.fft.rfft(seg, axis=1).mean(0)
    H = Y / np.where(np.abs(X) > 1e-12, X, 1e-12)
    r = np.fft.irfft(np.fft.rfft(seg[0]) * np.conj(X), n)
    p = int(np.argmax(np.abs(r)))
    a, b, c = np.abs(r[p - 1]), np.abs(r[p]), np.abs(r[(p + 1) % n])
    frac = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) != 0 else 0.0
    sign = 1.0 if r[p] >= 0 else -1.0
    # latency against the endless stream: position in the period relative to the absolute sample count
    lat = (p + frac + s0) % n
    # H relative to the absolute phase as well (the window start shifts the period circularly)
    f = np.fft.rfftfreq(n, 1 / FS)
    H = H * np.exp(-2j * np.pi * f * (s0 % n) / FS)
    return f, H, lat, sign


def change(a, b):
    """H_b / H_a with the latency difference removed; returns f, ratio, delay difference (ms)"""
    f, Ha, pa, _ = a; _, Hb, pb, _ = b
    n = (len(f) - 1) * 2
    d = pb - pa
    if d > n / 2:
        d -= n
    if d < -n / 2:
        d += n
    ratio = Hb / Ha * np.exp(2j * np.pi * f * d / FS)
    return f, ratio, d / FS * 1000.0


def band_mean(f, v, lo, hi):
    m = (f >= lo) & (f <= hi)
    return v[m]
