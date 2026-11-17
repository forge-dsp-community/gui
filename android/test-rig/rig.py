"""Test rig: the Forge DSP app in the avant4_virt Cuttlefish VM (our LineageOS 24 + mainline kernel), the DEV
Forge passed through by QEMU (QMP usb-host), the Forge's USB audio stays on the PC for test signals.
Never talks to anything but the VM serial (ADB_SERIAL); the head unit is never touched."""
import json, os, re, socket, subprocess, time

ADB = os.environ.get("ADB", os.path.expanduser("~/Android/Sdk/platform-tools/adb"))
SERIAL = os.environ.get("ADB_SERIAL", "0.0.0.0:6520")
QMP = os.environ.get("QMP_SOCK", "/tmp/cf_avd_1000/cvd-1/internal/qemu_monitor.sock")
PKG = "io.github.psych0d0g.forgedsp"
CP2102 = (0x10C4, 0xEA60)

assert SERIAL != "192.168.5.28:5555", "the head unit is not a test target"


def adb(*args, timeout=60, check=True):
    try:
        p = subprocess.run([ADB, "-s", SERIAL, *args], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        # the VM hangs now and then (software renderer): without check, as if nothing came back
        if check:
            raise
        return ""
    if check and p.returncode:
        raise RuntimeError(f"adb {args}: {p.stderr.strip()[:200]}")
    return p.stdout


def sh(cmd, timeout=60):
    return adb("shell", cmd, timeout=timeout)


# --- QEMU: plug and unplug the Forge -------------------------------------------------------------
def qmp(*cmds):
    s = socket.socket(socket.AF_UNIX); s.connect(QMP); f = s.makefile("rw")

    def rd():
        while True:
            m = json.loads(f.readline())
            if "event" not in m:
                return m
    rd(); f.write(json.dumps({"execute": "qmp_capabilities"}) + "\n"); f.flush(); rd()
    out = []
    for c in cmds:
        f.write(json.dumps(c) + "\n"); f.flush(); out.append(rd())
    s.close()
    return out


def plugged():
    r = qmp({"execute": "human-monitor-command", "arguments": {"command-line": "info usb"}})[0]
    return "forgeusb" in r.get("return", "")


def plug():
    if not plugged():
        qmp({"execute": "device_add", "arguments": {"driver": "usb-host", "bus": "xhci.0", "vendorid": CP2102[0],
                                                     "productid": CP2102[1], "id": "forgeusb"}})


def unplug():
    if plugged():
        qmp({"execute": "device_del", "arguments": {"id": "forgeusb"}})


# --- UI ------------------------------------------------------------------------------------------
def ui_nodes():
    # the dump fails now and then while a window animates: try again, an empty list when it never works
    xml = ""
    for _ in range(3):
        if adb("shell", "uiautomator dump /sdcard/rig_ui.xml >/dev/null 2>&1 && cat /sdcard/rig_ui.xml", check=False):
            xml = adb("shell", "cat /sdcard/rig_ui.xml", check=False) or ""
            if "<node" in xml:
                break
        time.sleep(1)
    nodes = []
    for m in re.finditer(r"<node [^>]*>", xml):
        a = dict(re.findall(r'(\w[\w-]*)="([^"]*)"', m.group(0)))
        b = re.findall(r"\d+", a.get("bounds", ""))
        if len(b) == 4:
            x1, y1, x2, y2 = map(int, b)
            a["center"] = ((x1 + x2) // 2, (y1 + y2) // 2)
        nodes.append(a)
    return nodes


def ui_find(text=None, rid=None, contains=False):
    for n in ui_nodes():
        t = n.get("text", "")
        if text is not None and (text in t if contains else t == text):
            return n
        if rid is not None and n.get("resource-id", "").endswith(rid):
            return n
    return None


def tap(node):
    x, y = node["center"]; sh(f"input tap {x} {y}")


def wake():
    """screen on and unlocked (the VM may sit on the lock screen after a screen-off test)"""
    sh("input keyevent 224; wm dismiss-keyguard; cmd statusbar collapse")
    time.sleep(1)


def accept_usb_dialog(always=True, wait=15):
    """the system dialogs on plugging in: 'Open … to handle …' / 'Allow … to access …'"""
    wake()
    t0 = time.time(); done = False
    while time.time() - t0 < wait:
        q = ui_find("to handle", contains=True) or ui_find("to access", contains=True)
        if q:
            if always:
                box = ui_find("Always", contains=True) or ui_find("Use by default", contains=True)
                if box and box.get("checked") == "false":
                    tap(box)
            ok = ui_find("OK") or ui_find("Allow")
            if ok:
                tap(ok); done = True; time.sleep(1.5); continue
        elif done:
            return True
        time.sleep(1)
    return done


def logcat_clear():
    adb("logcat", "-c", check=False)


def logcat(tag_regex=None, lines=2000):
    out = adb("logcat", "-d", "-t", str(lines), check=False)
    return [l for l in out.splitlines() if not tag_regex or re.search(tag_regex, l)]


# --- the Forge through the app (CommandReceiver; debug builds log the answers, tag ForgeCmd) ---------
import threading, itertools, base64, hashlib, struct, urllib.request


class Forge:
    """lines to the Forge the way other apps send them; answers from logcat"""
    def __init__(self):
        self.ids = itertools.count(int(time.time()) % 100000 * 1000)
        self.ans = {}
        self.events = []
        self.cv = threading.Condition()
        adb("logcat", "-c", check=False)
        self.p = subprocess.Popen([ADB, "-s", SERIAL, "logcat", "-v", "raw", "-s", "ForgeCmd:I"], stdout=subprocess.PIPE,
                                  text=True, bufsize=1)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for l in self.p.stdout:
            l = l.rstrip("\n")
            m = re.match(r"answer id=(\S*) error=(.*?) lines=(.*)$", l)
            with self.cv:
                if m:
                    self.ans[m.group(1)] = (m.group(2), [x for x in m.group(3).split("␞") if x])
                    self.cv.notify_all()
                elif l.startswith("event "):
                    self.events.append((time.time(), l[6:]))

    def send(self, line, timeout=15):
        """-> (error, [answer lines]); error '' = answered"""
        i = str(next(self.ids))
        q = line.replace("'", "'\\''")
        sh(f"am broadcast -a {PKG}.action.SEND -n {PKG}/.service.CommandReceiver --es line '{q}' --es id {i} >/dev/null")
        t0 = time.time()
        with self.cv:
            while i not in self.ans and time.time() - t0 < timeout:
                self.cv.wait(0.2)
            if i in self.ans:
                return self.ans.pop(i)
        # the logcat stream can miss a line; the buffer still has it
        for l in adb("logcat", "-d", "-v", "raw", "-s", "ForgeCmd:I", check=False).splitlines():
            m = re.match(r"answer id=(\S*) error=(.*?) lines=(.*)$", l)
            if m and m.group(1) == i:
                self.missed = getattr(self, "missed", 0) + 1
                return m.group(2), [x for x in m.group(3).split("\u241e") if x]
        return "timeout", []

    def get(self, reg):
        err, lines = self.send(f"get.1 {reg}")
        return dict(re.findall(r"<\.1 (\S+?)=(.*?)\s*$", "\n".join(lines), re.M)), err

    def set(self, reg, val, verify=True, read=None):
        err, lines = self.send(f"set.1 {reg}={val}")
        if not verify:
            return err, lines
        r = read or (reg[:-5] if reg.endswith(".calc") else reg)
        got, e2 = self.get(r)
        return ("" if got.get(r) == val else f"read back {got.get(r)!r} ({err or e2 or 'ok'})"), lines

    def online(self):
        err, lines = self.send("get.1 system.firmware.port", timeout=8)
        return err == "" and any("system.firmware.port=" in l for l in lines), err


def wait_online(f, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        ok, err = f.online()
        if ok:
            return time.time() - t0
        accept_usb_dialog(wait=2)
        time.sleep(1)
    return None


# --- Chrome DevTools of the app's WebView (debug build) ---------------------------------------------
class Cdp:
    """minimal CDP client over a stdlib WebSocket: evaluate JavaScript in the GUI"""
    PORT = 9333

    def __init__(self):
        pid = sh(f"pidof {PKG}").strip().split()[0]
        adb("forward", f"tcp:{self.PORT}", f"localabstract:webview_devtools_remote_{pid}")
        pages = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{self.PORT}/json", timeout=10).read())
        url = [p for p in pages if p.get("type") == "page"][0]["webSocketDebuggerUrl"]
        host, path = url[len("ws://"):].split("/", 1)
        self.s = socket.create_connection(("127.0.0.1", self.PORT), timeout=120)
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall(f"GET /{path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                       f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode())
        hdr = b""
        while b"\r\n\r\n" not in hdr:
            hdr += self.s.recv(1)
        assert b" 101 " in hdr.split(b"\r\n")[0], hdr
        self.n = 0

    def _send(self, txt):
        d = txt.encode(); mask = os.urandom(4)
        h = bytes([0x81]) + (bytes([0x80 | len(d)]) if len(d) < 126 else
                             bytes([0x80 | 126]) + struct.pack(">H", len(d)) if len(d) < 65536 else
                             bytes([0x80 | 127]) + struct.pack(">Q", len(d)))
        self.s.sendall(h + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(d)))

    def _recv(self):
        def rd(n):
            b = b""
            while len(b) < n:
                c = self.s.recv(n - len(b))
                if not c:
                    raise ConnectionError("devtools closed")
                b += c
            return b
        out = b""
        while True:
            b0, b1 = rd(2)
            ln = b1 & 0x7F
            if ln == 126: ln = struct.unpack(">H", rd(2))[0]
            elif ln == 127: ln = struct.unpack(">Q", rd(8))[0]
            out += rd(ln)
            if b0 & 0x80:
                return out.decode(errors="replace")

    def call(self, method, **params):
        self.n += 1; i = self.n
        self._send(json.dumps({"id": i, "method": method, "params": params}))
        while True:
            m = json.loads(self._recv())
            if m.get("id") == i:
                return m

    def js(self, expr, timeout_ms=30000):
        r = self.call("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True, timeout=timeout_ms)
        res = r.get("result", {})
        if "exceptionDetails" in res:
            raise RuntimeError(res["exceptionDetails"].get("text", "") + str(res["exceptionDetails"].get("exception", {}).get("description", "")))
        return res.get("result", {}).get("value")


def open_gui(timeout=60, front=True):
    """the app in front; returns once the GUI is loaded (debug builds: window.__forgeRig).
    front=False: only attach DevTools (an `am start` pauses the activity for a moment, which stalled
    a running firmware update)"""
    if front:
        sh(f"am start -n {PKG}/.ui.MainActivity >/dev/null")
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            c = Cdp()
            if c.js("typeof window.__forgeRig") == "object":
                return c
        except Exception:
            pass
        time.sleep(2)
    raise RuntimeError("GUI not ready (window.__forgeRig)")


def app_settings(cdp):
    return json.loads(cdp.js("window.ForgeAndroid ? window.ForgeAndroid.settings() : '{}'"))


def screenshot(path):
    """PNG of the VM screen"""
    with open(path, "wb") as f:
        p = subprocess.run([ADB, "-s", SERIAL, "exec-out", "screencap", "-p"], stdout=f, timeout=60)
    return p.returncode == 0 and os.path.getsize(path) > 1000


def install(apk):
    """debug builds of each CI run carry their own key: uninstall first (settings and the USB default go)"""
    adb("uninstall", PKG, check=False)
    out = adb("install", "-g", apk, timeout=300)
    assert "Success" in out, out
