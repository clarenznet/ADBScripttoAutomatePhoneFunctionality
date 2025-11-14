import subprocess
import time
import re
import xml.etree.ElementTree as ET
import os

USSD_CODE = "*123#"
POLL_INTERVAL = 3  # seconds between checking connected devices
COORDS_CACHE_FILE = "dialpad_coords_cache.txt"

def adb(cmd, capture=False, device=None):
    """Run adb command with optional device selection."""
    dev_arg = f"-s {device}" if device else ""
    full_cmd = f"adb {dev_arg} {cmd}"
    if capture:
        return subprocess.check_output(full_cmd, shell=True).decode(errors="ignore")
    else:
        subprocess.run(full_cmd, shell=True)

def get_connected_devices():
    """Return a list of connected device serial numbers."""
    out = adb("devices", capture=True)
    devices = []
    for line in out.splitlines()[1:]:
        if line.strip() and "device" in line:
            devices.append(line.split()[0])
    return devices

def is_screen_on(device):
    out = adb("shell dumpsys power | findstr 'Display Power'", capture=True, device=device)
    return "state=ON" in out

def wake_screen(device):
    if not is_screen_on(device):
        print(f"[{device}] 🔋 Waking screen...")
        adb("shell input keyevent 26", device=device)
        time.sleep(1)

def swipe_up_to_unlock(device):
    size = adb("shell wm size", capture=True, device=device)
    m = re.search(r"(\d+)x(\d+)", size)
    if not m:
        return
    w, h = map(int, m.groups())
    start_x = w // 2
    start_y = int(h * 0.8)
    end_y = int(h * 0.2)
    print(f"[{device}] ⬆️ Swiping up to unlock...")
    adb(f"shell input swipe {start_x} {start_y} {start_x} {end_y} 350", device=device)
    time.sleep(1)

def open_dialer(device):
    adb("shell am start -a android.intent.action.DIAL", device=device)
    time.sleep(2.5)

def get_ui_dump(device):
    adb("shell uiautomator dump /sdcard/dialer.xml", device=device)
    adb(f"pull /sdcard/dialer.xml dialer_{device}.xml", device=device)
    with open(f"dialer_{device}.xml", "r", encoding="utf-8") as f:
        return f.read()

def parse_dialpad_coords(xml):
    coords = {}
    tree = ET.fromstring(xml)
    for node in tree.iter("node"):
        text = node.attrib.get("text")
        bounds = node.attrib.get("bounds", "")
        if not text or text.strip() not in "0123456789*#":
            continue
        m = re.findall(r"\[(\d+),(\d+)\]", bounds)
        if len(m) == 2:
            (x1, y1), (x2, y2) = map(lambda p: (int(p[0]), int(p[1])), m)
            cx = (x1 + x2) // 2
            cy = (y1 + y2) // 2
            coords[text.strip()] = (cx, cy)
    return coords

def save_coords_cache(coords):
    with open(COORDS_CACHE_FILE, "w") as f:
        for k, v in coords.items():
            f.write(f"{k}:{v[0]},{v[1]}\n")

def load_coords_cache():
    coords = {}
    if not os.path.exists(COORDS_CACHE_FILE):
        return None
    with open(COORDS_CACHE_FILE, "r") as f:
        for line in f:
            k, val = line.strip().split(":")
            x, y = map(int, val.split(","))
            coords[k] = (x, y)
    return coords

def tap(device, x, y):
    adb(f"shell input tap {x} {y}", device=device)
    time.sleep(0.15)  # faster tapping speed

def dial_code(device, code, coords):
    for ch in code:
        if ch in coords:
            tap(device, *coords[ch])
        else:
            print(f"[{device}] ⚠️ Missing coordinate for '{ch}'")
    print(f"[{device}] ✅ Finished entering {code}")

def press_call(device):
    size = adb("shell wm size", capture=True, device=device)
    m = re.search(r"(\d+)x(\d+)", size)
    if m:
        w, h = map(int, m.groups())
        tap(device, w // 2, h - 180)
    print(f"[{device}] ☎️ Called USSD code.")

def process_device(device, coords):
    print(f"\n🚀 Processing {device} ...")
    wake_screen(device)
    swipe_up_to_unlock(device)
    open_dialer(device)
    dial_code(device, USSD_CODE, coords)
    press_call(device)
    print(f"[{device}] ✅ Done.\n")

# --- MAIN LOOP ---
print("🔁 Polling for new devices... (Ctrl+C to exit)")
known_devices = set()
coords_cache = load_coords_cache()

while True:
    try:
        current_devices = set(get_connected_devices())
        new_devices = current_devices - known_devices

        for dev in new_devices:
            print(f"\n📱 New device detected: {dev}")
            if coords_cache is None:
                print(f"[{dev}] Capturing dialer layout for the first time...")
                wake_screen(dev)
                swipe_up_to_unlock(dev)
                open_dialer(dev)
                xml = get_ui_dump(dev)
                coords_cache = parse_dialpad_coords(xml)
                save_coords_cache(coords_cache)
                print(f"[{dev}] Dial pad cached: {coords_cache.keys()}")
            process_device(dev, coords_cache)

        known_devices = current_devices
        time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        print("\n🛑 Stopped by user.")
        break
