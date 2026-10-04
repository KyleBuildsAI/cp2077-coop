"""Run a local two-instance coop test session on one PC.

Steps:
  1. back up UserSettings.json and apply the potato profile (unless --no-potato)
  2. start the local relay (optionally with simulated latency/jitter/loss)
  3. launch the host instance, then the joiner instance after --stagger seconds,
     both with -skipStartScreen; windows are placed side by side
  4. wait until both games exit, stop the relay, restore UserSettings.json

If anything goes wrong, restore the settings manually with:
    python coop-tools/test_session.py --restore-settings

Usage:
    python coop-tools/test_session.py HOST_GAME JOINER_GAME
    python coop-tools/test_session.py HOST_GAME JOINER_GAME --latency-ms 115 --jitter-ms 20 --loss-pct 1
"""
import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)
import potato_settings  # noqa: E402

SETTINGS_DIR = os.path.join(os.environ.get("LOCALAPPDATA", ""), "CD Projekt Red", "Cyberpunk 2077")
LIVE_SETTINGS = os.path.join(SETTINGS_DIR, "UserSettings.json")
BACKUP_SETTINGS = LIVE_SETTINGS + ".coopbak"
WINDOW_WIDTH, WINDOW_HEIGHT = 1280, 720


def game_running():
    output = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Cyberpunk2077.exe", "/FO", "CSV", "/NH"],
                            capture_output=True, text=True).stdout
    return "Cyberpunk2077.exe" in output


def apply_potato():
    if os.path.exists(BACKUP_SETTINGS):
        print(f"settings backup already exists ({BACKUP_SETTINGS}); potato profile is probably active, not backing up again")
        return
    shutil.copy2(LIVE_SETTINGS, BACKUP_SETTINGS)
    with open(BACKUP_SETTINGS, encoding="utf-8") as handle:
        settings = json.load(handle)
    changes = potato_settings.apply_profile(settings, potato_settings.build_profile(60, f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}", "off"))
    with open(LIVE_SETTINGS, "w", encoding="utf-8", newline="\r\n") as handle:
        json.dump(settings, handle, indent=4, ensure_ascii=False)
    print(f"potato profile applied ({len(changes)} options); your settings are backed up to {BACKUP_SETTINGS}")


def restore_settings():
    if not os.path.exists(BACKUP_SETTINGS):
        print("no settings backup found; nothing to restore")
        return
    if game_running():
        print("a game instance is still running; close it first, then run --restore-settings")
        return
    shutil.copy2(BACKUP_SETTINGS, LIVE_SETTINGS)
    os.replace(BACKUP_SETTINGS, BACKUP_SETTINGS + ".restored")
    print("your original graphics settings are restored")


def launch(game, caption):
    exe_dir = os.path.join(game, "bin", "x64")
    return subprocess.Popen([os.path.join(exe_dir, "Cyberpunk2077.exe"), "-skipStartScreen", "-windowCaption", caption],
                            cwd=exe_dir)


def place_window(pid, x, y, timeout=180.0):
    """Move the process' main window once it appears (best effort)."""
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def callback(hwnd, _):
        window_pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
        if window_pid.value == pid and user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
            found.append(hwnd)
        return True

    deadline = time.time() + timeout
    while time.time() < deadline:
        found.clear()
        user32.EnumWindows(callback, 0)
        if found:
            user32.MoveWindow(found[0], x, y, WINDOW_WIDTH + 16, WINDOW_HEIGHT + 39, True)
            return True
        time.sleep(1.0)
    return False


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("host_game", nargs="?")
    parser.add_argument("joiner_game", nargs="?")
    parser.add_argument("--latency-ms", type=float, default=0.0)
    parser.add_argument("--jitter-ms", type=float, default=0.0)
    parser.add_argument("--loss-pct", type=float, default=0.0)
    parser.add_argument("--stagger", type=float, default=40.0, help="seconds between host and joiner launch")
    parser.add_argument("--no-potato", action="store_true")
    parser.add_argument("--restore-settings", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    if args.restore_settings:
        restore_settings()
        return
    if not (args.host_game and args.joiner_game):
        sys.exit("give HOST_GAME and JOINER_GAME folders (or --restore-settings)")
    if game_running():
        sys.exit("Cyberpunk is already running; close it first")

    if not args.no_potato:
        apply_potato()

    relay = subprocess.Popen([sys.executable, os.path.join(TOOLS, "coop_relay.py"), "--latency-ms", str(args.latency_ms),
                              "--jitter-ms", str(args.jitter_ms), "--loss-pct", str(args.loss_pct)],
                             creationflags=subprocess.CREATE_NEW_CONSOLE)
    try:
        host = launch(args.host_game, "COOP HOST")
        print(f"host launched (pid {host.pid}); joiner in {args.stagger:.0f} s")
        place_window(host.pid, 0, 0)
        time.sleep(args.stagger)
        joiner = launch(args.joiner_game, "COOP JOINER")
        print(f"joiner launched (pid {joiner.pid})")
        place_window(joiner.pid, WINDOW_WIDTH + 20, 0)
        print("session running; close both games to finish (settings are restored automatically)")
        host.wait()
        joiner.wait()
    finally:
        relay.terminate()
        if not args.no_potato:
            time.sleep(2.0)
            restore_settings()


if __name__ == "__main__":
    main()
