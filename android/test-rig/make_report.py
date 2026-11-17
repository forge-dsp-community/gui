#!/usr/bin/env python3
"""HTML report of a test run (results/<run>/): verdicts, audio level curves of the monitor per test,
screenshots. Writes results/<run>/report.html; images stay beside it (published as artifact files)."""
import html, json, os, statistics, sys

HERE = os.path.dirname(os.path.abspath(__file__))
CSV = os.path.join(HERE, "results", "audio-levels.csv")

NAMES = {"Start": "Start", "T01": "Erstes Anstecken", "T02": "Hotplug-Zyklen", "T03": "Befehlsdurchsatz",
         "T04": "Lautstärke", "T05": "Gleichzeitigkeit", "T06": "Speichern über die GUI", "T07": "Lebenszyklus",
         "T08": "Forge-Neustart", "T10": "Dauerlauf", "T11": "Filter, EQ, Gain live am Signal"}


def rows(t0, t1):
    out = []
    with open(CSV) as f:
        for line in f:
            v = line.split(",")
            if len(v) == 7 and t0 <= float(v[0]) <= t1:
                out.append([float(x) for x in v])
    return out


def curve(t0, t1, ref, marks=()):
    """SVG: tone level L/R relative to the reference, noise floor beside the tone"""
    r = rows(t0 - 3, t1 + 3)
    if len(r) < 5:
        return "<p class='muted'>Keine Monitordaten für diesen Abschnitt.</p>"
    step = max(1, len(r) // 700); r = r[::step]
    W, H, L, B, T = 760, 220, 46, 28, 10
    x0, x1 = r[0][0], r[-1][0]
    lo, hi = -45.0, 12.0
    X = lambda t: L + (t - x0) / max(x1 - x0, 1e-6) * (W - L - 8)
    Y = lambda v: T + (hi - max(lo, min(hi, v))) / (hi - lo) * (H - T - B)
    def path(idx, base):
        return " ".join(f"{'M' if i == 0 else 'L'}{X(p[0]):.1f},{Y(p[idx] - base):.1f}" for i, p in enumerate(r))
    g = []
    for v in (-40, -30, -20, -10, 0, 10):
        g.append(f"<line x1='{L}' x2='{W - 8}' y1='{Y(v):.1f}' y2='{Y(v):.1f}' class='grid'/>"
                 f"<text x='{L - 6}' y='{Y(v) + 4:.1f}' class='ax' text-anchor='end'>{v:+d}</text>")
    dur = x1 - x0; tick = 10 if dur < 120 else 30 if dur < 400 else 60 if dur < 900 else 300
    t = 0
    while t <= dur:
        g.append(f"<line x1='{X(x0 + t):.1f}' x2='{X(x0 + t):.1f}' y1='{T}' y2='{H - B}' class='grid'/>"
                 f"<text x='{X(x0 + t):.1f}' y='{H - B + 16}' class='ax' text-anchor='middle'>{int(t)} s</text>")
        t += tick
    for tm, label in marks:
        if x0 <= tm <= x1:
            g.append(f"<line x1='{X(tm):.1f}' x2='{X(tm):.1f}' y1='{T}' y2='{H - B}' class='mark'/>"
                     f"<title>{html.escape(label)}</title>")
    floor = " ".join(f"{'M' if i == 0 else 'L'}{X(p[0]):.1f},{Y((p[3] + p[4]) / 2 - (ref[0] + ref[1]) / 2):.1f}" for i, p in enumerate(r))
    return (f"<figure class='chart'><svg viewBox='0 0 {W} {H}' role='img' aria-label='Pegel des Monitortons'>"
            + "".join(g) +
            f"<path d='{floor}' class='floor'/><path d='{path(1, ref[0])}' class='l'/><path d='{path(2, ref[1])}' class='r'/>"
            f"<text x='{L - 38}' y='{T + 4}' class='ax'>dB</text></svg>"
            "<figcaption><span class='key l'></span>Ausgang 1 (L) <span class='key r'></span>Ausgang 2 (R) "
            "<span class='key floor'></span>Rauschen neben dem Ton · Pegel relativ zum Start des Monitors"
            + (" · senkrechte Linien: Schritte" if marks else "") + "</figcaption></figure>")


def live_md(d, out_path):
    """interim: the verdicts as the running suite prints them, as the markdown of a finished run"""
    vt, cur, proto = [], "Start", []
    for line in open(out_path, errors="replace"):
        line = line.rstrip("\n"); proto.append(line)
        m = line.split(" ", 1)
        if m and len(m[0]) == 3 and m[0][0] == "T" and m[0][1:].isdigit():
            cur = m[0]
        st = line.strip()
        if st.startswith("OK ") or st.startswith("FEHLER "):
            ok = st.startswith("OK"); rest = st.split(" ", 1)[1].strip()
            det = ""
            if rest.endswith(")") and " (" in rest:
                rest, det = rest[:-1].split(" (", 1)
            vt.append(f"| {cur} | {rest} | {'OK' if ok else '**FEHLER**'} | {det} |")
    files = sorted(f for f in os.listdir(d) if f.endswith(".png"))
    shots = [(f, f.split("-", 1)[1].rsplit(".", 1)[0].replace("-", " ")) for f in files]
    return "\n".join(["| Test |", *vt, "", "```", *proto, "```"]), {"shots": shots, "windows": []}


def main(runs, live_out=None):
    """runs: one run, or several 'a,b' (earlier first; the last may be live); output in the last one"""
    names = runs.split(",")
    if len(names) > 1:
        import shutil
        last = os.path.join(HERE, "results", names[-1])
        md_parts, shots, windows, t11 = [], [], [], []
        for k, n in enumerate(names):
            dd = os.path.join(HERE, "results", n)
            lo = live_out if k == len(names) - 1 else None
            if lo:
                md, dat = live_md(dd, lo)
                if os.path.exists(os.path.join(dd, "data.json")):
                    sv = json.load(open(os.path.join(dd, "data.json")))
                    dat["windows"], dat["T11"], dat["shots"] = sv.get("windows", []), sv.get("T11", []), sv.get("shots", []) or dat["shots"]
            else:
                md = open(os.path.join(dd, "report.md")).read(); dat = json.load(open(os.path.join(dd, "data.json")))
            md_parts.append(md)
            for fn, cap in dat.get("shots", []):
                if dd != last:
                    shutil.copy(os.path.join(dd, fn), os.path.join(last, f"{k}{fn}")); fn = f"{k}{fn}"
                shots.append((fn, cap))
            windows += dat.get("windows", []); t11 += dat.get("T11", [])
        tables = [l for m in md_parts for l in m.split("```")[0].splitlines() if l.startswith("| ") and not l.startswith("| Test") and not l.startswith("|---")]
        protos = "\n".join(m.split("```")[1].strip() for m in md_parts if "```" in m)
        merged = "\n".join(["| Test |", *tables, "", "```", protos, "```"])
        open(os.path.join(last, "merged.md"), "w").write(merged)
        json.dump({"shots": shots, "windows": windows, "T11": t11}, open(os.path.join(last, "merged.json"), "w"))
        return main(names[-1] + "|merged", None)
    run = runs
    merged = run.endswith("|merged")
    if merged:
        run = run[:-7]
    d = os.path.join(HERE, "results", run)
    if merged:
        rep = open(os.path.join(d, "merged.md")).read(); data = json.load(open(os.path.join(d, "merged.json")))
        live_out = None
    elif False:
        pass
    elif live_out:
        rep, data = live_md(d, live_out)
        if os.path.exists(os.path.join(d, "data.json")):   # tests finished so far: their windows and shots
            saved = json.load(open(os.path.join(d, "data.json")))
            data["windows"] = saved.get("windows", [])
            data["T11"] = saved.get("T11", [])
            data["shots"] = saved.get("shots", []) or data["shots"]
    if not merged and not live_out:
        data = json.load(open(os.path.join(d, "data.json")))
        rep = open(os.path.join(d, "report.md")).read()
    # verdicts from the markdown table (the run keeps them there)
    vt = []
    for line in rep.splitlines():
        if line.startswith("| ") and not line.startswith("| Test") and not line.startswith("|---"):
            c = [x.strip() for x in line.strip("|").split("|")]
            if len(c) >= 4:
                vt.append((c[0], c[1], c[2] == "OK", c[3]))
    live_curve = ""
    if live_out and os.path.exists(CSV):
        import time as _t
        t_start = _t.mktime(_t.strptime(run, "%Y%m%d-%H%M%S"))
        live_curve = ("<section class='test'><header><h2><span class='tid'>Audio</span> Ton seit Beginn des Laufs</h2></header>"
                      + "%CURVE%" + "</section>")
    skipc = os.path.join(d, "skip_checks.txt")
    if os.path.exists(skipc):
        drop = set(l.strip() for l in open(skipc) if l.strip())
        vt = [v for v in vt if f"{v[0]}|{v[1]}" not in drop]
    else:
        drop = set()
    proto = rep.split("```")[1] if "```" in rep else ""
    if drop:
        names = [x.split("|", 1)[1] for x in drop]
        proto = "\n".join(l for l in proto.splitlines() if not ((l.strip().startswith("FEHLER") or l.strip().startswith("OK")) and any(n in l for n in names)))
    head = [l for l in proto.splitlines() if l.startswith("Ziel ") or l.startswith("Forge-Firmware")]
    # reference level: first 5 s of the monitor
    allr = rows(0, 1e12)
    ref = (statistics.median(x[1] for x in allr[:25]), statistics.median(x[2] for x in allr[:25])) if allr else (0, 0)
    ok = sum(v[2] for v in vt)
    tests = []
    for v in vt:
        if v[0] not in tests:
            tests.append(v[0])
    win = {w[0]: w for w in data.get("windows", [])}
    steps = {}
    for s in data.get("T11", []):
        steps.setdefault("T11", []).append((s["t0"], s["step"]))
    skip = set(open(os.path.join(d, "skip_shots.txt")).read().split()) if os.path.exists(os.path.join(d, "skip_shots.txt")) else set()
    shots = [x for x in data.get("shots", []) if x[0] not in skip]
    # a run during which only the line-in's noise arrived: no level curves (they would show noise)
    no_audio = os.path.exists(os.path.join(d, "no_audio"))
    sec = []
    for t in tests:
        vs = [v for v in vt if v[0] == t]
        bad = sum(not v[2] for v in vs)
        rowsh = "".join(f"<tr class='{'ok' if v[2] else 'bad'}'><td><span class='pill'>{'OK' if v[2] else 'Fehler'}</span></td>"
                        f"<td>{html.escape(v[1])}</td><td class='det'>{html.escape(v[3])}</td></tr>" for v in vs)
        ch = ""
        if t in win and not no_audio:
            w = win[t]
            ch = curve(w[1], w[2], ref, steps.get(t, []))
            if w[4]:
                ch = "<p class='muted'>Aussetzer in diesem Test sind erwartet (Neustart, Stummschaltung oder Lautstärke 0).</p>" + ch
        sh = "".join(f"<figure class='shot'><button type='button' class='zoom' data-src='{html.escape(n)}' data-cap='{html.escape(c)}' aria-label='{html.escape(c)} vergrößern'>"
                     f"<img src='{html.escape(n)}' alt='{html.escape(c)}' loading='lazy'></button><figcaption>{html.escape(c)}</figcaption></figure>"
                     for n, c in shots if n.split("-", 1)[1].startswith(t.lower()))
        sec.append(f"<section class='test' id='{t.lower()}'><header><h2><span class='tid'>{t}</span> {html.escape(NAMES.get(t, t))}</h2>"
                   f"<span class='state {'bad' if bad else 'ok'}'>{len(vs) - bad}/{len(vs)}</span></header>"
                   f"<table><tbody>{rowsh}</tbody></table>{ch}" + (f"<div class='shots'>{sh}</div>" if sh else "") + "</section>")
    if live_out and live_curve and not no_audio:
        sec.insert(0, live_curve.replace("%CURVE%", curve(t_start, _t.time(), ref)))
    nav = "".join(f"<a href='#{t.lower()}'>{t}</a>" for t in tests)
    sp = os.path.join(d, "spectral.html")
    spectral = (f"<section class='test' id='spektrum'><header><h2><span class='tid'>Spektrum</span> Filter und EQ mit rosa Rauschen</h2></header>"
                f"{open(sp).read()}</section>") if os.path.exists(sp) else ""
    fp = os.path.join(d, "findings.html")
    findings = (f"<section class='test findings'><header><h2><span class='tid'>Befunde</span> aus diesem Lauf</h2></header>"
                f"{open(fp).read()}</section>") if os.path.exists(fp) else ""
    status = (f"<p class='live'>Zwischenstand {__import__('time').strftime('%H:%M')} Uhr, der Lauf ist noch nicht fertig.</p>"
              if live_out else "")
    page = f"""<title>Forge-App Testlauf {run}</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
/* layout: instrument log, a narrow column of test sections, each with its checks, the level trace of the
   monitored tone and the screens; summary strip first */
:root {{ --bg:#f5f6f4; --panel:#ffffff; --fg:#1d2422; --muted:#5d6864; --line:#d9dedb; --accent:#1f6f8b;
  --l:#1f6f8b; --r:#b5651d; --floor:#9aa5a1; --ok:#2e7d4f; --bad:#b3261e;
  --sans:"IBM Plex Sans", system-ui, sans-serif; --mono:"IBM Plex Mono", ui-monospace, monospace; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --bg:#141917; --panel:#1b211f; --fg:#e4e9e6;
  --muted:#98a39f; --line:#2c3431; --accent:#5fb3cf; --l:#5fb3cf; --r:#e09a56; --floor:#5e6965; --ok:#5cbf84; --bad:#ef7b72; color-scheme:dark }} }}
:root[data-theme="dark"] {{ --bg:#141917; --panel:#1b211f; --fg:#e4e9e6; --muted:#98a39f; --line:#2c3431; --accent:#5fb3cf;
  --l:#5fb3cf; --r:#e09a56; --floor:#5e6965; --ok:#5cbf84; --bad:#ef7b72; color-scheme:dark }}
body {{ background:var(--bg); color:var(--fg); font:15px/1.55 var(--sans); }}
.wrap {{ max-width:980px; margin:0 auto; padding-block:28px 56px; padding-left:max(16px, env(safe-area-inset-left, 0px)); padding-right:max(16px, env(safe-area-inset-right, 0px)); display:grid; gap:22px; }}
h1 {{ font-size:1.6rem; margin:0; font-weight:600; text-wrap:balance; }}
h2 {{ font-size:1.12rem; margin:0; font-weight:600; display:flex; gap:.6rem; align-items:baseline; }}
.tid {{ font-family:var(--mono); color:var(--accent); font-weight:500; }}
.muted {{ color:var(--muted); margin:0; }}
.live {{ margin:0; font:500 .85rem var(--mono); color:var(--accent); }}
.sum {{ display:flex; flex-wrap:wrap; gap:10px 28px; align-items:baseline; font-family:var(--mono); font-size:.86rem; color:var(--muted); }}
.big {{ font:600 2.1rem/1 var(--sans); color:var(--fg); font-variant-numeric:tabular-nums; }}
nav {{ display:flex; flex-wrap:wrap; gap:6px; }}
nav a {{ font-family:var(--mono); font-size:.8rem; color:var(--accent); text-decoration:none; border:1px solid var(--line); padding:2px 8px; border-radius:4px; }}
nav a:focus-visible, nav a:hover {{ border-color:var(--accent); outline:none; }}
.test {{ background:var(--panel); border:1px solid var(--line); border-radius:6px; padding:16px 18px; display:grid; gap:12px; min-width:0; }}
.test header {{ display:flex; justify-content:space-between; gap:12px; align-items:baseline; flex-wrap:wrap; }}
.state {{ font-family:var(--mono); font-size:.85rem; font-variant-numeric:tabular-nums; }}
.state.ok {{ color:var(--ok); }} .state.bad {{ color:var(--bad); font-weight:600; }}
table {{ border-collapse:collapse; width:100%; font-size:.9rem; }}
td {{ border-top:1px solid var(--line); padding:6px 8px 6px 0; vertical-align:top; }}
td.det {{ color:var(--muted); font-family:var(--mono); font-size:.78rem; overflow-wrap:anywhere; }}
.pill {{ font:500 .72rem var(--mono); letter-spacing:.04em; text-transform:uppercase; padding:1px 7px; border-radius:3px; border:1px solid currentColor; }}
tr.ok .pill {{ color:var(--ok); }} tr.bad .pill {{ color:var(--bad); }} tr.bad td {{ font-weight:500; }}
.chart {{ margin:0; overflow-x:auto; }}
.chart svg {{ width:100%; min-width:520px; height:auto; display:block; }}
.grid {{ stroke:var(--line); stroke-width:1; }} .ax {{ fill:var(--muted); font:11px var(--mono); }}
.mark {{ stroke:var(--accent); stroke-width:1; stroke-dasharray:2 3; opacity:.7; }}
path.l {{ fill:none; stroke:var(--l); stroke-width:1.5; }} path.r {{ fill:none; stroke:var(--r); stroke-width:1.5; }}
path.floor {{ fill:none; stroke:var(--floor); stroke-width:1; stroke-dasharray:4 3; }}
figcaption {{ font-size:.78rem; color:var(--muted); margin-top:4px; }}
.key {{ display:inline-block; width:14px; height:3px; vertical-align:middle; margin:0 4px 0 10px; background:var(--l); }}
.key.r {{ background:var(--r); }} .key.floor {{ background:var(--floor); }} figcaption .key:first-child {{ margin-left:0; }}
.findings ul {{ margin:0; padding-left:1.1rem; display:grid; gap:8px; }} .findings li {{ max-width:75ch; }}
.findings a {{ color:var(--accent); }}
.specs {{ display:grid; grid-template-columns:repeat(auto-fill, minmax(min(100%, 420px), 1fr)); gap:14px; }}
.spec {{ margin:0; min-width:0; }} .spec svg {{ width:100%; height:auto; display:block; }}
.spec figcaption {{ margin:0 0 4px; font-size:.82rem; color:var(--fg); }}
path.model {{ fill:none; stroke:var(--accent); stroke-width:2; }} path.ref {{ fill:none; stroke:var(--r); stroke-width:1.2; stroke-dasharray:4 3; }}
circle.pt {{ fill:var(--fg); }} circle.pt.off {{ fill:var(--floor); opacity:.5; }}
.okt {{ color:var(--ok); }} .badt {{ color:var(--bad); font-weight:600; }}
.shots {{ display:grid; grid-template-columns:repeat(auto-fill, minmax(min(100%, 280px), 1fr)); gap:12px; }}
.shot {{ margin:0; }} .shot img {{ width:100%; border:1px solid var(--line); border-radius:4px; display:block; }}
button.zoom {{ all:unset; display:block; cursor:zoom-in; border-radius:4px; }}
button.zoom:focus-visible {{ outline:2px solid var(--accent); outline-offset:2px; }}
dialog#lb {{ border:0; padding:0; background:transparent; max-width:96vw; max-height:96vh; }}
dialog#lb::backdrop {{ background:rgba(8,12,11,.82); }}
#lb-img {{ display:block; max-width:96vw; max-height:88vh; width:auto; height:auto; border-radius:4px; cursor:zoom-out; }}
#lb-cap {{ margin:8px 0 0; color:#e4e9e6; font-size:.85rem; text-align:center; }}
.lb-close {{ position:absolute; top:8px; right:8px; background:rgba(20,25,23,.8); color:#fff; border:0; border-radius:4px; width:32px; height:32px; font-size:16px; cursor:pointer; }}
pre {{ background:var(--panel); border:1px solid var(--line); border-radius:6px; padding:12px; font:12px/1.5 var(--mono); overflow-x:auto; color:var(--fg); }}
@media (prefers-reduced-motion: reduce) {{ * {{ scroll-behavior:auto; }} }}
</style>
<div class="wrap">
<header style="display:grid; gap:8px">
<h1>Forge-App mit dem DEV-Forge</h1>
<p class="muted">Testlauf {run}: Android-App (Debug-Build) in der VM avant4_virt, DEV-Forge 96ELN per USB durchgereicht, rosa Rauschen (−12 dBFS) über das USB-Audio des Forge, Ausgänge am Line-In des PCs ausgewertet. Alle Einstellungen über die App.</p>{"<p style='border-left:3px solid #c77d00;padding:.4em .9em'>Audioprüfungen dieses Laufs entfernt: Während des Laufs kam am Line-In nur das Grundrauschen an (−42 dBFS, mit und ohne Signal gleich). Die Signalprüfungen werden nachgeholt, sobald der Messweg wieder Signal führt. Die Funktionsprüfungen der App gelten.</p>" if no_audio else ""}
{status}
<div class="sum"><span><span class="big">{ok}/{len(vt)}</span> Prüfungen bestanden</span>{"".join(f"<span>{html.escape(h)}</span>" for h in head)}</div>
<nav>{nav}</nav>
</header>
{findings}
{spectral}
{"".join(sec)}
<section><h2>Protokoll</h2><pre>{html.escape(proto.strip())}</pre></section>
</div>
<dialog id="lb" aria-label="Screenshot in voller Größe">
  <button type="button" class="lb-close" aria-label="Schließen">✕</button>
  <img id="lb-img" alt="">
  <p id="lb-cap"></p>
</dialog>
<script>
(() => {{
  const dlg = document.getElementById("lb"), img = document.getElementById("lb-img"), cap = document.getElementById("lb-cap");
  const items = [...document.querySelectorAll("button.zoom")];
  let i = 0;
  const show = (k) => {{ i = (k + items.length) % items.length; img.src = items[i].dataset.src; img.alt = items[i].dataset.cap; cap.textContent = items[i].dataset.cap; }};
  items.forEach((b, k) => b.addEventListener("click", () => {{ show(k); dlg.showModal(); }}));
  dlg.addEventListener("click", (e) => {{ if (e.target === dlg || e.target === img || e.target.classList.contains("lb-close")) dlg.close(); }});
  dlg.addEventListener("keydown", (e) => {{ if (e.key === "ArrowRight") show(i + 1); if (e.key === "ArrowLeft") show(i - 1); }});
}})();
</script>
"""
    open(os.path.join(d, "report.html"), "w").write(page)
    print(os.path.join(d, "report.html"), len(shots), "Screenshots")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
