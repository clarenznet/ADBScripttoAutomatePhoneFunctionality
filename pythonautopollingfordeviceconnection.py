#!/usr/bin/env python3
"""
fast_multi_dialer_fixed.py

- Multithreaded ADB poller: when a new device is plugged it will wake,
  swipe, open dialer, discover dialpad coordinates once (cached) and
  then type the USSD code by tapping real dialpad buttons, then press CALL.

- Works on Windows/Linux/macOS. If UI dump can't find enough keys it
  falls back to an automatic grid based on screen size.

Usage: python fast_multi_dialer_fixed.py
"""

import subprocess, time, threading, re, xml.etree.ElementTree as ET, os, sys

# ---------- CONFIG ----------
USSD_CODE = "*#*#1234#*#*"           # change to your USSD
POLL_INTERVAL = 2             # seconds between device polls
TAP_DELAY = 0.08              # global tap delay (seconds) — tune between 0.05..0.12
COORDS_CACHE_FILE = "dialpad_coords_cache.txt"
# ----------------------------

lock = threading.Lock()
global_coords = None  # cached coords dict { '1':(x,y), ..., '*':(x,y), 'CALL':(x,y) }

# ---------- adb helpers ----------
def adb_cmd(device, cmd, capture=False):
    dev = f"-s {device} " if device else ""
    full = f"adb {dev}{cmd}"
    if capture:
        return subprocess.check_output(full, shell=True).decode(errors="ignore")
    else:
        subprocess.call(full, shell=True)

def list_devices():
    out = adb_cmd(None, "devices", capture=True)
    devs = []
    for line in out.splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        if "\tdevice" in line:
            devs.append(line.split()[0])
    return devs

# ---------- screen / unlock ----------
def is_screen_on(device):
    try:
        out = adb_cmd(device, "shell dumpsys power", capture=True)
        return ("state=ON" in out) or ("mHoldingDisplaySuspendBlocker=true" in out) or ("mWakefulness=Awake" in out)
    except Exception:
        return False

def wake_and_swipe(device):
    # Wake
    try:
        if not is_screen_on(device):
            print(f"[{device}] Waking screen")
            adb_cmd(device, "shell input keyevent 26")
            time.sleep(0.6)
        else:
            print(f"[{device}] Screen already on")
    except Exception as e:
        print(f"[{device}] Wake error: {e}")
    # swipe up to dismiss wallpaper/lock (adjustable)
    try:
        size = adb_cmd(device, "shell wm size", capture=True)
        m = re.search(r"(\d+)x(\d+)", size)
        if m:
            w, h = map(int, m.groups())
            sx, sy = w//2, int(h*0.8)
            ex, ey = w//2, int(h*0.25)
            adb_cmd(device, f"shell input swipe {sx} {sy} {ex} {ey} 300")
            time.sleep(0.6)
        else:
            # default swipe coords
            adb_cmd(device, "shell input swipe 360 1200 360 400 300")
            time.sleep(0.6)
    except Exception as e:
        print(f"[{device}] Swipe error: {e}")

# ---------- UI dump parsing ----------
def dump_ui(device, out_path=None):
    # create dump on device and pull it
    try:
        adb_cmd(device, "shell uiautomator dump /sdcard/dialer.xml")
        local = out_path if out_path else f"dialer_{device}.xml"
        adb_cmd(device, f"pull /sdcard/dialer.xml {local}")
        with open(local, "r", encoding="utf-8") as f:
            data = f.read()
        return data
    except Exception as e:
        print(f"[{device}] UI dump error: {e}")
        return None

def parse_coords_from_ui(xml_string):
    coords = {}
    try:
        tree = ET.fromstring(xml_string)
    except Exception:
        return coords
    # Try to find nodes where text is a dial character, or resource-id/content-desc includes digit
    for node in tree.iter("node"):
        text = node.attrib.get("text", "").strip()
        desc = node.attrib.get("content-desc", "").strip()
        resid = node.attrib.get("resource-id", "").strip()
        bounds = node.attrib.get("bounds", "")
        key = None
        if text and text in "0123456789*#":
            key = text
        else:
            # search resource-id or content-desc for digit or words like "dialpad","keypad","star","pound","call"
            for ch in "0123456789*#":
                if ch in resid or ch in desc:
                    key = ch
                    break
            # some buttons have no text but resid like com.android.dialer:id/zero or desc "zero"
            if not key:
                m = re.search(r"(zero|one|two|three|four|five|six|seven|eight|nine|star|pound|hash|call)", (resid+" "+desc).lower())
                if m:
                    name = m.group(1)
                    mapping = {"zero":"0","one":"1","two":"2","three":"3","four":"4","five":"5","six":"6","seven":"7","eight":"8","nine":"9","star":"*","pound":"#","hash":"#","call":"CALL"}
                    key = mapping.get(name)
        if key and bounds:
            m = re.findall(r"\[(\d+),(\d+)\]", bounds)
            if len(m) == 2:
                (x1,y1),(x2,y2) = m
                cx = (int(x1)+int(x2))//2
                cy = (int(y1)+int(y2))//2
                coords[key] = (cx,cy)
    return coords

# ---------- fallback grid ----------
def fallback_grid(device):
    # compute a safe 3x4 dialpad grid from screen size
    size = adb_cmd(device, "shell wm size", capture=True)
    m = re.search(r"(\d+)x(\d+)", size)
    if not m:
        # return a reasonable default for 720x1612
        w,h = 720,1612
    else:
        w,h = map(int, m.groups())
    # choose region near bottom half for dialpad
    top = int(h*0.45)
    bottom = int(h*0.85)
    left = int(w*0.12)
    right = int(w*0.88)
    # 3 columns, 4 rows
    cols = [left, w//2, right]
    rows = [top + (bottom-top)*i//4 for i in range(4)]
    mapping = ['1','2','3','4','5','6','7','8','9','*','0','#']
    coords = {}
    i=0
    for r in rows:
        for c in cols:
            coords[mapping[i]] = (c, r)
            i+=1
    # CALL button approximate bottom center
    coords['CALL'] = (w//2, int(h*0.93))
    return coords

# ---------- save/load cache ----------
def save_coords(coords):
    try:
        with open(COORDS_CACHE_FILE, "w") as f:
            for k,v in coords.items():
                f.write(f"{k}:{v[0]},{v[1]}\n")
    except Exception as e:
        print("Cache write error:", e)

def load_coords():
    if not os.path.exists(COORDS_CACHE_FILE):
        return None
    coords={}
    try:
        with open(COORDS_CACHE_FILE,"r") as f:
            for line in f:
                if ':' in line:
                    k,val = line.strip().split(":")
                    x,y = map(int,val.split(","))
                    coords[k]= (x,y)
        return coords
    except Exception as e:
        print("Cache read error:", e)
        return None

# ---------- interaction ----------
def tap(device,x,y):
    adb_cmd(device, f"shell input tap {x} {y}")
    time.sleep(TAP_DELAY)

def ensure_keypad_open(device, coords):
    # Try to find a 'dialpad' or keypad button in UI first (resource-id/content-desc)
    # If not found, tap bottom-center to open keypad.
    # Look for nodes whose resource-id/content-desc contain 'dialpad' or 'keypad' or 'show dialpad'.
    xml = dump_ui(device)
    if xml:
        tree = ET.fromstring(xml)
        for node in tree.iter("node"):
            resid = node.attrib.get("resource-id","").lower()
            desc = node.attrib.get("content-desc","").lower()
            text = node.attrib.get("text","").lower()
            if any(k in resid for k in ("dialpad","keypad","show_dialpad","show_dialer","toggle")) or any(k in desc for k in ("dialpad","keypad","show dialpad")):
                bounds = node.attrib.get("bounds","")
                m = re.findall(r"\[(\d+),(\d+)\]", bounds)
                if len(m)==2:
                    (x1,y1),(x2,y2)=m
                    cx=(int(x1)+int(x2))//2; cy=(int(y1)+int(y2))//2
                    tap(device,cx,cy)
                    time.sleep(0.5)
                    return
    # fallback: tap bottom-center (safe)
    size = adb_cmd(device,"shell wm size", capture=True)
    m = re.search(r"(\d+)x(\d+)", size)
    if m:
        w,h = map(int,m.groups())
        tap(device, w//2, int(h*0.78))
    else:
        tap(device,360,1200)
    time.sleep(0.5)

# ---------- main per-device sequence ----------
def prepare_coords_for_device(device):
    global global_coords
    with lock:
        if global_coords:
            return global_coords
    # attempt UI parse
    xml = dump_ui(device)
    coords = {}
    if xml:
        coords = parse_coords_from_ui(xml)
    # if CALL not found or fewer than 10 keys -> fallback to grid
    if len([k for k in coords.keys() if k in "0123456789*#"]) < 10 or 'CALL' not in coords:
        print(f"[{device}] UI parse incomplete ({len(coords)} keys). Using fallback grid.")
        coords = fallback_grid(device)
    else:
        # try to find CALL button if missing using node text/res-id 'call','dial'
        if 'CALL' not in coords:
            try:
                tree = ET.fromstring(xml)
                for node in tree.iter("node"):
                    resid = node.attrib.get("resource-id","").lower()
                    desc = node.attrib.get("content-desc","").lower()
                    text = node.attrib.get("text","").lower()
                    if any(k in (resid+desc+text) for k in ("call","dial","dialer","phone","endcall")):
                        b=node.attrib.get("bounds","")
                        m=re.findall(r"\[(\d+),(\d+)\]",b)
                        if len(m)==2:
                            (x1,y1),(x2,y2)=m
                            coords['CALL']=((int(x1)+int(x2))//2,(int(y1)+int(y2))//2)
                            break
            except Exception:
                pass
    with lock:
        global_coords = coords
        save_coords(coords)
    print(f"[{device}] Using dialpad coords keys: {sorted(k for k in coords.keys())}")
    return coords

def process_device(device):
    try:
        print(f"\n[{device}] Detected — starting process.")
        adb_cmd(device, "wait-for-device")
        wake_and_swipe(device)
        ensure_keypad_open(device, None)
        adb_cmd(device, "shell am start -a android.intent.action.DIAL")
        time.sleep(1.2)
        coords = prepare_coords_for_device(device)
        # type code by tapping coords (use mapping)
        for ch in USSD_CODE:
            if ch in coords:
                x,y = coords[ch]
                tap(device, x, y)
            else:
                print(f"[{device}] Missing char '{ch}' in coords — skipping")
        # press CALL: prefer CALL coord if available
        if 'CALL' in coords:
            cx,cy = coords['CALL']
            tap(device, cx, cy)
        else:
            # fallback: bottom-center
            size = adb_cmd(device, "shell wm size", capture=True)
            m = re.search(r"(\d+)x(\d+)", size)
            if m:
                w,h = map(int,m.groups()); tap(device, w//2, int(h*0.93))
            else:
                tap(device, 360, 1500)
        print(f"[{device}] Done dialing (USSD attempted).")
    except Exception as e:
        print(f"[{device}] Error during process: {e}")

# ---------- poll loop with multithreading ----------
def main_loop():
    seen = set()
    # try load cache first
    global global_coords
    cached = load_coords()
    if cached:
        global_coords = cached
        print("Loaded cached dialpad coords.")
    print("🔁 Polling for new devices... (Ctrl+C to stop)")
    try:
        while True:
            current = set(list_devices())
            new = current - seen
            for dev in new:
                seen.add(dev)
                # start thread
                t = threading.Thread(target=process_device, args=(dev,), daemon=True)
                t.start()
            # remove disconnected devices from seen
            gone = seen - current
            if gone:
                for g in gone:
                    seen.remove(g)
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        print("\nStopped by user")

if __name__ == "__main__":
    main_loop()
