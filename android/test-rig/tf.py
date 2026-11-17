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


def measure(t0, t1, ch, lo=300.0, hi=6000.0):
    """(f, H, latency in samples, polarity) of channel ch. H is averaged over all periods of the window
    and referred to the endless stream (absolute sample count), so windows can be compared directly.
    Latency: coarse from the averaged circular cross-correlation, fine from the slope of the phase of H
    (weighted by |H|) between lo and hi; polarity from the sign of H there after removing the latency."""
    x = period(); n = len(x)
    rec = recording(t0, t1)
    if rec is None or len(rec[0]) < 2 * n:
        return None
    y, s0 = rec
    k = len(y) // n
    seg = y[:k * n, ch].reshape(k, n)
    X = np.fft.rfft(x); f = np.fft.rfftfreq(n, 1 / FS)
    Y = np.fft.rfft(seg, axis=1).mean(0)
    H = Y / np.where(np.abs(X) > 1e-12, X, 1e-12)
    H = H * np.exp(-2j * np.pi * f * (s0 % n) / FS)          # refer to the absolute stream position
    r = np.fft.irfft(H * np.abs(X) ** 2, n)                   # impulse-like correlation of the average
    p = int(np.argmax(np.abs(r)))
    band = (f >= 200) & (f <= 16000)
    strong = np.abs(H) > np.abs(H[band]).max() * 10 ** (-15 / 20)   # where the output passes (its own filters)
    m = band & strong
    w = np.abs(H[m]) ** 2
    G = H[m] * np.exp(2j * np.pi * f[m] * p / FS)             # what is left after the coarse latency
    ph = np.unwrap(np.angle(G))
    slope = np.sum(w * (f[m] - np.average(f[m], weights=w)) * (ph - np.average(ph, weights=w))) / np.sum(w * (f[m] - np.average(f[m], weights=w)) ** 2)
    lat = p - slope / (2 * np.pi) * FS
    sign = 1.0 if np.real(np.sum(w * np.exp(1j * (ph - slope * f[m])))) >= 0 else -1.0
    return f, H, lat % n, sign


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


def periods(t0, t1, ch):
    """per period of the window: (time, H referred to the absolute stream, latency in samples)"""
    x = period(); n = len(x)
    rec = recording(t0, t1)
    if rec is None or len(rec[0]) < n:
        return []
    y, s0 = rec
    X = np.fft.rfft(x); f = np.fft.rfftfreq(n, 1 / FS)
    out = []
    for k in range(len(y) // n):
        seg = y[k * n:(k + 1) * n, ch]
        H = np.fft.rfft(seg) / np.where(np.abs(X) > 1e-12, X, 1e-12)
        H = H * np.exp(-2j * np.pi * f * ((s0 + k * n) % n) / FS)
        out.append((t0 + (k + 0.5) * n / FS, H, _latency(f, H, X)))
    return out


def _latency(f, H, X):
    n = (len(f) - 1) * 2
    r = np.fft.irfft(H * np.abs(X) ** 2, n)
    p = int(np.argmax(np.abs(r)))
    band = (f >= 200) & (f <= 16000)
    m = band & (np.abs(H) > np.abs(H[band]).max() * 10 ** (-15 / 20))
    w = np.abs(H[m]) ** 2
    G = H[m] * np.exp(2j * np.pi * f[m] * p / FS)
    ph = np.unwrap(np.angle(G)); fm = np.average(f[m], weights=w)
    slope = np.sum(w * (f[m] - fm) * (ph - np.average(ph, weights=w))) / np.sum(w * (f[m] - fm) ** 2)
    lat = p - slope / (2 * np.pi) * FS
    n2 = n / 2
    return (lat + n2) % n - n2


def _rel(f, H, H0, X):
    """delay of H against H0 in samples: coarse from the cross-correlation, fine from the phase slope
    of H * conj(H0) (the system's own phase cancels), weighted by |H0|^2 where the output passes"""
    n = (len(f) - 1) * 2
    C = H * np.conj(H0)
    r = np.fft.irfft(C * np.abs(X) ** 2, n)
    p = int(np.argmax(np.abs(r)))
    band = (f >= 200) & (f <= 16000)
    m = band & (np.abs(H0) > np.abs(H0[band]).max() * 10 ** (-15 / 20))
    w = np.abs(C[m])
    ph = np.unwrap(np.angle(C[m] * np.exp(2j * np.pi * f[m] * p / FS))); fm = np.average(f[m], weights=w)
    slope = np.sum(w * (f[m] - fm) * (ph - np.average(ph, weights=w))) / np.sum(w * (f[m] - fm) ** 2)
    d = p - slope / (2 * np.pi) * FS
    return (d + n / 2) % n - n / 2


def abc(ref_periods, test_periods):
    """drift line through the delays of the reference periods (before and after the change) relative to
    the first one; every period is referred to that line; returns f, H_test / H_ref, delay change of the
    test periods (ms), polarity (+1/-1), pass-band mask, drift (ppm)"""
    x = period(); X = np.fft.rfft(x); n = len(x)
    f = np.fft.rfftfreq(n, 1 / FS)
    H0 = ref_periods[0][1]
    t0 = ref_periods[0][0]
    rd = lambda pp: np.array([_rel(f, H, H0, X) for _, H, _ in pp])
    tr = np.array([p[0] for p in ref_periods]) - t0; dr = rd(ref_periods)
    b, a = np.polyfit(tr, dr, 1)
    tt = np.array([p[0] for p in test_periods]) - t0; dt = rd(test_periods)
    def refd(pp, ts):
        return np.mean([H * np.exp(2j * np.pi * f * (a + b * t) / FS) for (_, H, _), t in zip(pp, ts)], 0)
    Href, Htest = refd(ref_periods, tr), refd(test_periods, tt)
    dl = dt - (a + b * tt)
    ratio = Htest / Href
    band = (f >= 200) & (f <= 16000)
    m = band & (np.abs(Href) > np.abs(Href[band]).max() * 10 ** (-15 / 20))
    pol = 1.0 if np.real(np.sum(ratio[m] * np.abs(Href[m]) ** 2)) >= 0 else -1.0
    return f, ratio, float(np.median(dl)) / FS * 1000, pol, m, b / FS * 1e6


def _peak(c):
    k = int(np.argmax(np.abs(c)))
    if 0 < k < len(c) - 1:
        a, b, d = np.abs(c[k - 1]), np.abs(c[k]), np.abs(c[k + 1])
        den = a - 2 * b + d
        return k + (0.5 * (a - d) / den if den else 0.0)
    return float(k)


def passband(f, H, lo=200.0, hi=16000.0, drop=15.0):
    """bins of the third-octave bands within `drop` dB of the strongest band (where the output passes;
    single bins are useless: interference lines and the dips of the pink period stand out there)"""
    m = np.zeros(len(f), bool)
    edges = 1000 * 2 ** (np.arange(-30, 25) / 3)
    bands = [(a, b) for a, b in zip(edges[:-1], edges[1:]) if a >= lo and b <= hi]
    lv = [np.mean(np.abs(H[(f >= a) & (f < b)]) ** 2) for a, b in bands]
    top = max(lv)
    for (a, b), l in zip(bands, lv):
        if l >= top * 10 ** (-drop / 10):
            m |= (f >= a) & (f < b)
    return m


def _peaks(z, X, n):
    """position (sub-sample, modulo n) of the circular cross-correlation peak of every row of z against
    the source period (spectrum X); robust at low SNR: the whole period is the processing gain"""
    r = np.fft.irfft(np.fft.rfft(z, axis=1) * np.conj(X)[None, :], n, axis=1)
    out = []
    for row in r:
        k = int(np.argmax(np.abs(row)))
        a, b, c = np.abs(row[k - 1]), np.abs(row[k]), np.abs(row[(k + 1) % n])
        den = a - 2 * b + c
        out.append(k + (0.5 * (a - c) / den if den else 0.0))
    return np.array(out)


def span(ta, tb0, tb1, tc1, ch):
    """A (ta..tb0, unchanged), B (tb0..tb1, changed), C (tb1..tc1, unchanged again) from ONE contiguous
    recording. The Forge and the line-in run on different clocks (some 100 ppm): the correlation peak
    of every period against the source gives the drift, the recording is resampled to exactly n samples
    per period, a second pass gives the residual line from A and C and the delay of B against it.
    Returns f, H_B/H_ref, delay change of B (ms), polarity, pass-band mask, clock offset (ppm), SNR (dB),
    number of periods A/B/C."""
    x = period(); n = len(x); X = np.fft.rfft(x)
    rec = recording(ta, tc1)
    if rec is None:
        return None
    y = rec[0][:, ch]
    unwrapn = lambda p: np.unwrap(p * 2 * np.pi / n) * n / (2 * np.pi)
    # pass 1: raw chunks of n
    k = len(y) // n
    p1 = unwrapn(_peaks(y[:k * n].reshape(k, n), X, n))
    j = np.arange(k)
    sb = (tb0 - ta) * FS / n, (tb1 - ta) * FS / n        # switch times in chunks
    ref1 = (j + 1 < sb[0] - 1) | (j > sb[1] + 1)
    slope = np.polyfit(j[ref1], p1[ref1], 1)[0]           # samples per chunk
    L = n - slope                                          # recorded samples per source period
    # pass 2: resampled to n per period
    k = int((len(y) - 2) / L)
    z = np.interp(np.arange(k * n) * (L / n), np.arange(len(y)), y).reshape(k, n)
    tp = (np.arange(k) + 0.5) * L / FS                     # seconds from ta
    isA = tp + 1.5 * L / FS <= tb0 - ta
    isB = (tp - 1.5 * L / FS >= tb0 - ta) & (tp + 1.5 * L / FS <= tb1 - ta)
    isC = tp - 1.5 * L / FS >= tb1 - ta
    ref = isA | isC
    p2 = unwrapn(_peaks(z, X, n))
    b, a = np.polyfit(tp[ref], p2[ref], 1)
    line = a + b * tp
    f = np.fft.rfftfreq(n, 1 / FS)
    H = np.fft.rfft(z, axis=1) / np.where(np.abs(X) > 1e-12, X, 1e-12)
    Hc = H * np.exp(2j * np.pi * f[None, :] * line[:, None] / FS)    # every period referred to the line
    Href, Hb = Hc[ref].mean(0), Hc[isB].mean(0)
    msk = passband(f, Href)
    noise = np.mean(np.abs(Hc[ref][:, msk] - Href[msk]) ** 2) / ref.sum()
    snr = 10 * np.log10(np.mean(np.abs(Href[msk]) ** 2) / noise)
    ratio = Hb / Href
    dl = float(np.median(p2[isB] - line[isB]))
    pol = 1.0 if np.real(np.sum(ratio[msk] * np.abs(Href[msk]) ** 2)) >= 0 else -1.0
    ppm = (L / n - 1) * 1e6 + b / FS * 1e6
    # band values: complex ratio averaged per third-octave, weighted with |H_ref|^2
    edges = 1000 * 2 ** (np.arange(-30, 25) / 3)
    fb, rb, wb = [], [], []
    w = np.abs(Href) ** 2
    for lo, hi in zip(edges[:-1], edges[1:]):
        mm = msk & (f >= lo) & (f < hi)
        if mm.sum() >= 8:
            fb.append(np.sqrt(lo * hi)); rb.append(np.sum(Hb[mm] * np.conj(Href[mm])) / np.sum(w[mm])); wb.append(np.sum(w[mm]))
    fb, rb, wb = np.array(fb), np.array(rb), np.array(wb)
    # fine delay: coarse from the peaks, the rest from the phase slope of the band values
    res = np.angle(rb * np.exp(2j * np.pi * fb * dl / FS))
    tr = -np.sum(wb * fb * res) / np.sum(wb * fb ** 2) / (2 * np.pi) * FS if len(fb) else 0.0
    return {"f": f, "ratio": ratio, "href": Href, "mask": msk, "delay_ms": (dl + tr) / FS * 1000, "coarse_ms": dl / FS * 1000,
            "pol": pol, "ppm": ppm, "snr": snr, "n": (int(isA.sum()), int(isB.sum()), int(isC.sum())),
            "fb": fb, "rb": rb, "wb": wb}


def band_model(R, resp):
    """model resp(f) (complex) averaged like the band values of span()"""
    f, msk, w = R["f"], R["mask"], np.abs(R["href"]) ** 2
    edges = 1000 * 2 ** (np.arange(-30, 25) / 3)
    out = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        mm = np.where(msk & (f >= lo) & (f < hi))[0]
        if len(mm) >= 8:
            mm = mm[::max(1, len(mm) // 40)]
            out.append(np.sum(np.array([resp(x) for x in f[mm]]) * w[mm]) / np.sum(w[mm]))
    return np.array(out)
