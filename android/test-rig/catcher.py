#!/usr/bin/env python3
"""Catches the digital input dropout (#80 candidate): random preset changes, EQ sweeps and biquad
writes from the PC (CP2102 at the PC), after every step G7/G8 and the monitored tone; on a dropout:
CT7302 register dump against the good one, then the recoveries in order."""
import json, os, random, re, serial, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tests import level_at

s = serial.Serial("/dev/ttyUSB0", 38400, timeout=0.05)
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results", time.strftime("catcher-%Y%m%d-%H%M%S"))
os.makedirs(OUT, exist_ok=True)
log = open(os.path.join(OUT, "log.txt"), "a", buffering=1)


def say(m):
    l = time.strftime("%H:%M:%S ") + m; print(l, flush=True); log.write(l + "\n")


def q(c, t=1.5):
    s.reset_input_buffer(); s.write((c + "\r\n").encode()); out = b""; e = time.time() + t
    while time.time() < e:
        d = s.read(8192); out += d
        if d: e = max(e, time.time() + 0.25)
    return out.decode(errors="replace")


def get(reg):
    m = re.search(r"<\.1 " + re.escape(reg) + r"=(\S*)", q("get.1 " + reg)); return m.group(1) if m else None


def vu():
    q("get.1 system.vu"); time.sleep(1.3)
    v = get("system.vu"); return [int(x) for x in v.strip("[]").split(",")] if v else []


def ct_dump(bus=1, addr=32):
    regs = {}
    for r in range(0, 128):
        v = get(f"i2c.{bus}.{addr}.{r}")
        if v:
            regs[r] = v.split(",")[0]
    return regs


def ok_now():
    g = vu(); l = level_at(time.time() - 1.5, time.time())
    return (len(g) == 8 and g[6] > 150 and g[7] > 150), g, l


if __name__ == "__main__":
    minutes = float(sys.argv[1]) if len(sys.argv) > 1 else 30
    good = ct_dump(); json.dump(good, open(os.path.join(OUT, "ct7302-gut.json"), "w"), indent=0)
    say(f"Start, {len(good)} CT7302-Register gesichert, Lauf {minutes} min")
    rnd = random.Random(80); t_end = time.time() + minutes * 60; k = 0
    while time.time() < t_end:
        k += 1
        op = rnd.choice(["preset", "preset", "sweep", "bq", "gain"])
        if op == "preset":
            q(f"set.1 preset.loaded={rnd.choice([0, 1])}"); time.sleep(rnd.uniform(0.3, 5))
        elif op == "sweep":
            for g in list(range(0, 61, 5)) + list(range(55, -1, -5)):
                s.write(f"set.1 biquad.1.5.calc=[1,7100,{g},2000,0,128]\r\n".encode()); time.sleep(0.05)
            time.sleep(1); q("")
        elif op == "bq":
            ch = rnd.randint(1, 8); q(f"set.1 biquad.{ch}.6.calc=[1,{rnd.randint(100, 8000)},{rnd.randint(-60, 30)},1414,0,{rnd.choice([0, 128])}]")
        else:
            q(f"set.1 gain.out.{rnd.randint(1, 8)}={rnd.randint(205, 223)}")
        if k % 3 == 0 or op == "preset":
            time.sleep(2)
            ok, g, l = ok_now()
            if not ok:
                time.sleep(3); ok, g, l = ok_now()
            if not ok:
                say(f"AUSFALL nach Schritt {k} ({op}): G1..G8 {g}, Line-In {l}")
                bad = ct_dump(); json.dump(bad, open(os.path.join(OUT, "ct7302-ausfall.json"), "w"), indent=0)
                diff = {r: (good.get(r), v) for r, v in bad.items() if good.get(r) != v}
                say(f"CT7302 Register anders als im guten Zustand: {diff}")
                for name, cmd in [("mixer.in 3->2", ["set.1 mixer.in=3", "set.1 mixer.in=2"]),
                                  ("Preset neu laden", ["set.1 preset.loaded=0", "set.1 preset.loaded=1"])]:
                    for c in cmd:
                        q(c); time.sleep(4)
                    ok2, g2, _ = ok_now(); say(f"Rettung {name}: {'hilft' if ok2 else 'hilft nicht'} {g2}")
                    if ok2:
                        break
                break
    else:
        say(f"kein Ausfall in {k} Schritten")
    q("set.1 biquad.1.5.calc=[0,0,0,0,0,0]")
