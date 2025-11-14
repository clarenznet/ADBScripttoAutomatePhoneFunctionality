import subprocess
import time
import threading
import csv
import os
import sys
import shutil

#M10 package name and activity 12 hours
APP_PACKAGE = "com.sprd.validationtools"
APP_ACTIVITY = "com.zyt.agingtest.AgingTest"



#S34 package name and activity
#APP_PACKAGE = "com.zyt.agingtest"
#APP_ACTIVITY = ".AgingTest"

# Global stats
processed_devices = 0
total_time = 0.0
lock = threading.Lock()
device_last_seen = {}
processed_sessions = {}
CSV_FILE = "M10AgingStart_log.csv"

# Cooldown time before rechecking the same connected device (in seconds)
RECHECK_INTERVAL = 30


# === 📦 ADB PORTABLE SETUP ===
def get_adb_path():
    """
    Detect if running from PyInstaller bundle or raw script,
    and return absolute path to adb executable.
    """
    if getattr(sys, 'frozen', False):  # running from .exe
        base_path = sys._MEIPASS  # temp folder created by PyInstaller
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))

    adb_folder = os.path.join(base_path, "platform-tools")
    adb_exe = os.path.join(adb_folder, "adb.exe")

    if not os.path.isfile(adb_exe):
        print("⚠️ 'adb.exe' not found in platform-tools folder.")
        print("   Expected path:", adb_exe)
        print("   Please ensure platform-tools/ folder is bundled next to this app.")
        sys.exit(1)

    return adb_exe


ADB_PATH = get_adb_path()


def adb(cmd, device=None):
    """Run adb command safely with bundled ADB."""
    base = [ADB_PATH]
    if device:
        base += ["-s", device]
    result = subprocess.run(base + cmd.split(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return result.stdout.strip()


def is_screen_on(device):
    output = adb("shell dumpsys power | findstr 'Display Power'", device)
    return "state=ON" in output or "mHoldingDisplaySuspendBlocker=true" in output


def wake_and_unlock(device):
    """Wake and unlock the screen safely."""
    if not is_screen_on(device):
        print(f"💡 {device}: Screen OFF, turning ON...")
        adb("shell input keyevent 26", device)
        time.sleep(1)
    else:
        print(f"💡 {device}: Screen already ON.")

    adb("shell input keyevent 82", device)
    time.sleep(0.5)
    adb("shell input swipe 500 1500 500 300", device)
    time.sleep(1)


def is_app_running(device):
    """Check if app process is active."""
    output = adb(f"shell pidof {APP_PACKAGE}", device)
    return bool(output.strip())


def check_foreground(device):
    """Check if app is in the foreground."""
    output = adb("shell dumpsys window windows | findstr mCurrentFocus", device)
    return APP_PACKAGE in output


#def launch_app(device):
 #   """Launch the app."""
  #  adb(f"shell am start -n {APP_PACKAGE}/{APP_ACTIVITY}", device)
def launch_app(device):
    """Launch the app using the SECRET_CODE broadcast (e.g., secret code 4321)."""
    secret_code = "4321"
    # Send broadcast that mimics dialing the secret code
    adb(f"shell am broadcast -a android.provider.Telephony.SECRET_CODE -d android_secret_code://{secret_code}", device)
    print(f"🚀 {device}: Sent SECRET_CODE broadcast for {secret_code}")
#def launch_app(device):
 #   """Launch the app with extras (e.g., secret code 4321)."""
  #  secret_code = "4321"
   # # Replace "secret_code" with the actual key the app expects (check logcat if unsure)
    #adb(f"shell am start -n {APP_PACKAGE}/{APP_ACTIVITY} --es secret_code {secret_code}", device)

def log_to_csv(device, status, duration):
    """Log to CSV file."""
    file_exists = os.path.isfile(CSV_FILE)
    with open(CSV_FILE, mode="a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["Device", "Status", "Duration (s)", "Timestamp"])
        writer.writerow([device, status, f"{duration:.2f}", time.strftime("%Y-%m-%d %H:%M:%S")])


def process_device(device):
    """Process the connected device once per connection."""
    global processed_devices, total_time
    start_time = time.time()

    # If recently processed, skip until cooldown
    if device in processed_sessions and (time.time() - processed_sessions[device]) < RECHECK_INTERVAL:
        return

    processed_sessions[device] = time.time()

    print(f"\n📱 Processing device: {device}")

    wake_and_unlock(device)

    # Check if app already running
    if is_app_running(device):
        if check_foreground(device):
            print(f"✅ {device}: App already running and visible. Skipping.")
            status = "Already Running"
        else:
            print(f"⚠️ {device}: App running but not in foreground, bringing forward...")
            launch_app(device)
            time.sleep(3)
            if check_foreground(device):
                print(f"✅ {device}: Brought app to foreground.")
                status = "Brought to Foreground"
            else:
                print(f"❌ {device}: Failed to bring app to foreground.")
                status = "Foreground Failed"
    else:
        print(f"🚀 {device}: App not running, launching now...")
        launch_app(device)
        time.sleep(3)

        if check_foreground(device):
            print(f"✅ {device}: App launched successfully.")
            status = "Launched"
        else:
            print(f"⚠️ {device}: App not detected in foreground, retrying once...")
            launch_app(device)
            time.sleep(3)
            if check_foreground(device):
                print(f"✅ {device}: App launched successfully after retry.")
                status = "Launched After Retry"
            else:
                print(f"❌ {device}: App failed to launch after retry.")
                status = "Failed"

    # Log and update stats
    duration = time.time() - start_time
    log_to_csv(device, status, duration)

    with lock:
        processed_devices += 1
        total_time += duration
        avg_time = total_time / processed_devices
        print(f"\n📊 Stats Update:")
        print(f"   Total devices processed: {processed_devices}")
        print(f"   Total time: {total_time:.2f}s")
        print(f"   Average time per phone: {avg_time:.2f}s\n")


def poll_devices():
    """Continuously poll for connected devices."""
    print("🔁 Polling for connected devices... (Ctrl+C to stop)")

    while True:
        result = subprocess.run([ADB_PATH, "devices"], stdout=subprocess.PIPE, text=True).stdout
        lines = [line.strip() for line in result.splitlines() if "\tdevice" in line]
        current_devices = [line.split("\t")[0] for line in lines]
        current_time = time.time()

        for device in current_devices:
            if device not in device_last_seen or (current_time - device_last_seen[device]) > 5:
                device_last_seen[device] = current_time
                threading.Thread(target=process_device, args=(device,), daemon=True).start()

        # Clean disconnected devices
        for device in list(device_last_seen.keys()):
            if device not in current_devices:
                del device_last_seen[device]
                processed_sessions.pop(device, None)

        time.sleep(3)


if __name__ == "__main__":
    print("🚀 Starting Portable Android Auto Launcher...")
    print(f"📂 Using ADB from: {ADB_PATH}")
    poll_devices()
