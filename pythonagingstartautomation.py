import subprocess
import time
import re
import xml.etree.ElementTree as ET

USSD_CODE = "*#*#2828#*#*"

def adb(cmd, capture=False):
    """Run an adb command."""
    if capture:
        return subprocess.check_output(f"adb {cmd}", shell=True).decode(errors="ignore")
    else:
        subprocess.run(f"adb {cmd}", shell=True)

def wake_and_unlock():
    adb("shell input keyevent 26")  # Power
    time.sleep(1)
    adb("shell input keyevent 82")  # Unlock
    time.sleep(1)

def open_dialer():
    adb("shell am start -a android.intent.action.DIAL")
    time.sleep(2)

def get_ui_dump():
    """Dump current UI layout and return its XML as a string."""
    adb("shell uiautomator dump /sdcard/dialer.xml")
    adb("pull /sdcard/dialer.xml .")
    with open("dialer.xml", "r", encoding="utf-8") as f:
        xml = f.read()
    return xml

def parse_dialpad_coords(xml):
    """Parse coordinates for digits, *, and # buttons."""
    coords = {}
    tree = ET.fromstring(xml)

    for node in tree.iter("node"):
        text = node.attrib.get("text")
        res_id = node.attrib.get("resource-id", "")
        bounds = node.attrib.get("bounds", "")

        if not text:
            continue

        if text.strip() in list("0123456789*#"):
            # Extract coordinates from bounds like [x1,y1][x2,y2]
            m = re.findall(r"\[(\d+),(\d+)\]", bounds)
            if len(m) == 2:
                (x1, y1), (x2, y2) = map(lambda p: (int(p[0]), int(p[1])), m)
                # Center of button
                cx = (x1 + x2) // 2
                cy = (y1 + y2) // 2
                coords[text.strip()] = (cx, cy)

    return coords

def tap(x, y):
    adb(f"shell input tap {x} {y}")
    time.sleep(0.25)

def dial_code(code, coords):
    """Tap detected buttons in the correct order."""
    for ch in code:
        if ch in coords:
            tap(*coords[ch])
        else:
            print(f"⚠️ No coordinate for key '{ch}'")

def press_call():
    """Tap call button — bottom center usually."""
    size = adb("shell wm size", capture=True)
    m = re.search(r"(\d+)x(\d+)", size)
    if m:
        w, h = map(int, m.groups())
        tap(w // 2, h - 180)

# --- RUN ---
wake_and_unlock()
open_dialer()

print("📸 Capturing dialer layout...")
xml = get_ui_dump()
coords = parse_dialpad_coords(xml)

print("✅ Dialpad detected:", coords)

if len(coords) < 10:
    print("⚠️ Could not detect all dialpad keys. Try increasing delay or ensure dialer is visible.")
else:
    dial_code(USSD_CODE, coords)
    press_call()
    print("✅ USSD dial completed.")
