"""CP2077 Coop live monitor.

Watches the coop mod's stats and event files and the relay server, checks every
value against expected ranges, prints a dashboard and keeps a history log.

The mod writes (in bin/x64/plugins/cyber_engine_tweaks/mods/CP2077Coop):
    coop_stats_<role>.txt   the latest [STATS] line, rewritten every 5 s
    coop_events.log         every [CP2077Coop] log line with the time
CET's own print() output (bin/x64/plugins/cyber_engine_tweaks/scripting.log)
is buffered and shared by all mods, so the monitor does not read it.

Usage (from the game folder):
    python coop-tools/coop_monitor.py
    python coop-tools/coop_monitor.py --game "G:/SteamLibrary/steamapps/common/Cyberpunk 2077"
    python coop-tools/coop_monitor.py --once          # single check, then exit

Only the Python standard library is used.
"""
import argparse
import datetime
import glob
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

MOD_DIR = os.path.join("bin", "x64", "plugins", "cyber_engine_tweaks", "mods", "CP2077Coop")
SERVER_INI = os.path.join("red4ext", "plugins", "CP2077Coop", "server.ini")
REDSCRIPT_LOG = os.path.join("r6", "logs", "redscript_rCURRENT.log")
STATS_FILE_PATTERN = "coop_stats_*.txt"
EVENTS_FILE = "coop_events.log"
# CET writes the mod's Lua runtime errors here
LUA_ERROR_LOG = "CP2077Coop.log"
LUA_ERROR_RECENT_SECONDS = 600.0
# written by coop_relay.py next to this script (local two-instance tests)
RELAY_LOG = "coop_relay.log"
RELAY_WINDOW_BLOCKS = 6
RELAY_CLIENT = re.compile(r"\[STATS\]\s+#(\d+) \S+\s+in=\s*[\d.]+/s total=(\d+) gaps=(\d+) late=\d+ fwd=(\d+) dropped=(\d+)")
EVENT_KEYWORDS = ("EVENT", "WORLD SYNC", "synced", "ERROR", "FAILED", "GAVE UP", "disabled", "loaded",
                  "sync ON", "sync OFF", "not compiled")
RED4EXT_LOG_DIR = os.path.join("red4ext", "logs")

REFRESH_SECONDS = 5.0
STATS_STALE_SECONDS = 15.0

# Expected ranges. Route: Los Angeles <-> Warsaw relay <-> Russia.
# LA -> Warsaw ICMP is ~185 ms; player-to-player RTT adds the other leg,
# frame time and send-slot waits, so ~250-400 ms is normal for this route.
# The in-game panel grades with the same numbers (Diag.LIMITS in init.lua):
# change both together.
EXPECT = {
    "server_ping_ms": (250, 400),     # warn above, bad above
    "rtt_ms": (450, 700),
    "missed_pct": (10.0, 25.0),       # last 5 s, without packets overwritten by a low local fps
    "age_ms": (300, 1500),
    "avatar_err_m": (2.5, 5.0),
}
MIN_PPS = 20.0

GOOD, WARN, BAD, INFO = "OK  ", "WARN", "BAD ", "    "
COLORS = {GOOD: "\033[92m", WARN: "\033[93m", BAD: "\033[91m", INFO: "\033[0m"}
RESET = "\033[0m"


def parse_args():
    default_game = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser = argparse.ArgumentParser(description="CP2077 Coop live monitor")
    parser.add_argument("--game", default=default_game, help="game folder (contains bin, r6, red4ext)")
    parser.add_argument("--once", action="store_true", help="run one check and exit")
    parser.add_argument("--no-geo", action="store_true", help="skip relay location lookup (ip-api.com)")
    return parser.parse_args()


def read_server_address(game_dir):
    path = os.path.join(game_dir, SERVER_INI)
    values = {}
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                if "=" in line:
                    key, value = line.strip().split("=", 1)
                    values[key.strip()] = value.strip()
    except OSError as error:
        return None, None, f"cannot read {SERVER_INI}: {error}"
    return values.get("server_ip"), values.get("server_port"), None


def ping_ms(host):
    """One ICMP ping via the OS tool; returns milliseconds or None."""
    command = ["ping", "-n", "1", "-w", "1500", host] if os.name == "nt" else ["ping", "-c", "1", "-W", "2", host]
    try:
        output = subprocess.run(command, capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.search(r"[=<]\s*(\d+(?:\.\d+)?)\s*ms", output)
    return float(match.group(1)) if match else None


def relay_location(ip):
    try:
        with urllib.request.urlopen(f"http://ip-api.com/json/{ip}?fields=country,city,org", timeout=5) as response:
            data = json.load(response)
        return f"{data.get('city', '?')}, {data.get('country', '?')} ({data.get('org', '?')})"
    except (OSError, ValueError):
        return None


def tail_lines(path, max_bytes=200_000):
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            return handle.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return []


def file_age_seconds(path):
    try:
        return time.time() - os.path.getmtime(path)
    except OSError:
        return None


def parse_stats(line):
    return dict(re.findall(r"(\w+)=(\S+)", line.split("[STATS]", 1)[1]))


def read_stats_files(mod_dir):
    """[(role, age_seconds, stats)] for every fresh coop_stats_<role>.txt."""
    blocks = []
    for path in sorted(glob.glob(os.path.join(mod_dir, STATS_FILE_PATTERN))):
        age = file_age_seconds(path)
        if age is None or age > STATS_STALE_SECONDS:
            continue
        stats_lines = [line for line in tail_lines(path) if "[STATS]" in line]
        if not stats_lines:
            continue  # caught mid-rewrite; the next refresh reads it
        role = os.path.basename(path)[len("coop_stats_"):-len(".txt")]
        blocks.append((role, age, parse_stats(stats_lines[-1])))
    return blocks


def recent_events(mod_dir):
    lines = tail_lines(os.path.join(mod_dir, EVENTS_FILE))
    return [line for line in lines if "[STATS]" not in line and any(word in line for word in EVENT_KEYWORDS)]


def check_relay_log(tools_dir):
    """Ground truth from a local coop_relay.py: what each game's DLL skipped and what the link dropped."""
    path = os.path.join(tools_dir, RELAY_LOG)
    age = file_age_seconds(path)
    if age is None or age > STATS_STALE_SECONDS:
        return []
    blocks = []
    for line in tail_lines(path, 50_000):
        if "[STATS] clients=" in line:
            blocks.append({})
            continue
        match = RELAY_CLIENT.search(line)
        if match and blocks:
            blocks[-1][match.group(1)] = tuple(int(match.group(i)) for i in range(2, 6))
    if not blocks:
        return []
    # the relay logs cumulative counters every 5 s: compare with ~30 s ago
    previous = blocks[max(0, len(blocks) - 1 - RELAY_WINDOW_BLOCKS)] if len(blocks) > 1 else {}
    window_seconds = 5 * min(RELAY_WINDOW_BLOCKS, len(blocks) - 1)
    checks = []
    for client_id, values in sorted(blocks[-1].items()):
        total, gaps, forwarded, dropped = (now - before for now, before in zip(values, previous.get(client_id, (0, 0, 0, 0))))
        if total + gaps <= 0:
            continue
        dropped_pct = 100.0 * dropped / (forwarded + dropped) if forwarded + dropped else 0.0
        checks.append((INFO, f"relay client #{client_id}",
                       f"its DLL skipped {100.0 * gaps / (total + gaps):.1f} %, link dropped {dropped_pct:.1f} % "
                       f"(last {window_seconds or 'few'} s; the partner sees both as missed)"))
    return checks


def check_lua_errors(mod_dir):
    """Lua runtime errors CET logged for the mod during this game session."""
    path = os.path.join(mod_dir, LUA_ERROR_LOG)
    age = file_age_seconds(path)
    if age is None or age > LUA_ERROR_RECENT_SECONDS:
        return []
    errors = [line for line in tail_lines(path) if "stack traceback" in line or "attempt to" in line or "error" in line.lower()]
    if not errors:
        return []
    return [(WARN, "lua errors", f"{len(errors)} in {LUA_ERROR_LOG}, last: {errors[-1].strip()[-110:]}")]


def as_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def grade(value, limits, higher_is_bad=True):
    if value is None:
        return INFO
    warn_at, bad_at = limits
    if higher_is_bad:
        return GOOD if value < warn_at else (WARN if value < bad_at else BAD)
    return GOOD if value >= warn_at else BAD


def check_startup_logs(game_dir):
    """Compile errors, crashes, plugin loading."""
    checks = []
    redscript = tail_lines(os.path.join(game_dir, REDSCRIPT_LOG))
    errors = [l for l in redscript if "[ERROR" in l]
    if not redscript:
        checks.append((WARN, "redscript", "no log yet (game not started?)"))
    elif errors:
        checks.append((BAD, "redscript", f"{len(errors)} compile error(s): {errors[0][-120:]}"))
    else:
        checks.append((GOOD, "redscript", "compiled"))

    log_dir = os.path.join(game_dir, RED4EXT_LOG_DIR)
    try:
        newest = max((os.path.join(log_dir, n) for n in os.listdir(log_dir) if n.startswith("red4ext")), key=os.path.getmtime)
    except (OSError, ValueError):
        newest = None
    if newest:
        lines = tail_lines(newest)
        text = "\n".join(lines)
        checks.append((GOOD if "Codeware" in text and "has been loaded" in text else BAD, "Codeware", "loaded" if "Codeware" in text else "NOT loaded"))
        checks.append((GOOD if "CP2077 Coop" in text else BAD, "Coop DLL", "loaded" if "CP2077 Coop" in text else "NOT loaded"))
        crash = [l for l in lines if "Crash report" in l or "Watchdog" in l or "Message:" in l]
        if crash:
            checks.append((BAD, "crash", " | ".join(c.split("] ")[-1] for c in crash[-2:])))
    return checks


def write_panel_status(mod_dir, server, ping, location):
    """Shared with the in-game panel (init.lua reads monitor_status.txt)."""
    lines = [f"server={server}", f"updated={int(time.time())}"]
    if ping is not None:
        lines.append(f"server_ping_ms={ping:.0f}")
    if location:
        lines.append(f"server_location={location}")
    try:
        with open(os.path.join(mod_dir, "monitor_status.txt"), "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError as error:
        print(f"could not write monitor_status.txt: {error}")


def append_history(history_path, row):
    new_file = not os.path.exists(history_path)
    with open(history_path, "a", encoding="utf-8") as handle:
        if new_file:
            handle.write(",".join(row.keys()) + "\n")
        handle.write(",".join(str(v) for v in row.values()) + "\n")


def render(checks, events):
    os.system("cls" if os.name == "nt" else "clear")
    print(f"CP2077 Coop monitor   {datetime.datetime.now():%H:%M:%S}   (Ctrl+C to stop)\n")
    for level, name, detail in checks:
        print(f"  {COLORS[level]}[{level}]{RESET} {name:<22} {detail}")
    if events:
        print("\n  recent events:")
        for line in events[-8:]:
            print("   ", line.replace("[CP2077Coop] ", "")[:120])


def grade_stats(stats):
    """Dashboard rows for one [STATS] line."""
    checks = []
    rtt = as_float(stats.get("rtt_ms"))
    missed = as_float(stats.get("missed_pct"))
    age = as_float(stats.get("age_ms"))
    err = as_float(stats.get("avatar_err_m"))
    pps = as_float(stats.get("pps_in"))
    fps = as_float(stats.get("fps"))
    peer_rate = as_float(stats.get("peer_rate"))
    merged = as_float(stats.get("out_merged_pct"))
    state = stats.get("state", "?")
    if stats.get("sync", "on") == "off" or state == "PAUSED":
        # older builds have no sync= key: they always reported as if in gameplay
        return [(INFO, "connection", f"PAUSED  role={stats.get('role')} - you are in a menu / loading, network not graded")]
    checks.append((GOOD if state == "OK" else (WARN if state in ("WAITING", "STALE") else BAD), "connection", f"{state}  role={stats.get('role')}"))
    checks.append((GOOD if state == "OK" else WARN, "players", "2 / 2" if state == "OK" else "1 / 2 (no live partner)"))
    checks.append((grade(rtt, EXPECT["rtt_ms"]), "player round trip", f"{stats.get('rtt_ms')} ms  (min {stats.get('rtt_min')} / max {stats.get('rtt_max')}, {stats.get('rtt_n')} samples)"))
    # the game reads the DLL once per frame: at most min(partner's send rate, local fps) packets/s
    readable = min(peer_rate, fps) if fps and peer_rate else None
    pps_floor = 0.8 * readable if readable else MIN_PPS
    packets = f"{stats.get('pps_in')} read / {stats.get('pps_out')} sent per s"
    if readable:
        packets += f"  (partner sends {stats.get('peer_rate')}/s, your fps {stats.get('fps')})"
    checks.append((grade(pps, (pps_floor, pps_floor), higher_is_bad=False), "packets in / out", packets))
    if fps is not None and peer_rate and fps < 0.9 * peer_rate:
        checks.append((WARN, "frame rate", f"{fps:.0f} fps < partner's {peer_rate:.0f} packets/s: {stats.get('overwritten_pct')} % "
                                           "overwritten in the DLL before the game read them (local, not the network)"))
    if merged is not None and merged >= 10.0:
        checks.append((WARN, "packets out", f"{merged:.1f} % of your 30 Hz ticks fell in one frame and went out as one packet "
                                            "(your fps): the partner counts them as missed"))
    checks.append((grade(missed, EXPECT["missed_pct"]), "missed packets",
                   f"{stats.get('missed_pct')} % last 5 s, session {stats.get('missed_total_pct', '-')} %  "
                   f"(network loss or the partner's DLL merging ticks; late {stats.get('ignored')})"))
    checks.append((grade(age, EXPECT["age_ms"]), "last packet age", f"{stats.get('age_ms')} ms"))
    checks.append((grade(err, EXPECT["avatar_err_m"]), "avatar drift", f"{stats.get('avatar_err_m')} m"))
    if stats.get("conflict") == "true":
        checks.append((BAD, "roles", "BOTH players have the same role - one must switch in the coop panel"))
    if stats.get("peer_old") == "true":
        checks.append((WARN, "partner version", "no ping replies - partner probably on an older mod version"))
    return checks


def run(args):
    game_dir = os.path.abspath(args.game)
    mod_dir = os.path.join(game_dir, MOD_DIR)
    history_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "coop_monitor_history.csv")

    if not os.path.isdir(mod_dir):
        print(f"Coop mod not found in {game_dir}. Pass --game with the game folder.")
        return 2

    try:
        import devkit  # same folder
        devkit.write_modlist(game_dir)
    except (ImportError, OSError) as error:
        print(f"could not refresh modlist.txt: {error}")

    ip, port, ini_error = read_server_address(game_dir)
    location = None if args.no_geo or not ip else relay_location(ip)

    while True:
        checks = [(INFO, "game folder", game_dir)]
        if ini_error:
            checks.append((BAD, "server.ini", ini_error))
        server_ping = ping_ms(ip) if ip else None
        checks.append((INFO, "relay server", f"{ip}:{port}" + (f"  {location}" if location else "")))
        checks.append((grade(server_ping, EXPECT["server_ping_ms"]) if server_ping is not None else BAD,
                       "your ping to relay", f"{server_ping:.0f} ms" if server_ping is not None else "no reply (ICMP blocked or offline)"))
        if ip:
            write_panel_status(mod_dir, f"{ip}:{port}", server_ping, location)

        checks.extend(check_startup_logs(game_dir))

        checks.extend(check_lua_errors(mod_dir))
        checks.extend(check_relay_log(os.path.dirname(os.path.abspath(__file__))))
        events = recent_events(mod_dir)

        stats_blocks = read_stats_files(mod_dir)
        if not stats_blocks:
            checks.append((WARN, "in-game stats", f"none in the last {STATS_STALE_SECONDS:.0f} s - game closed, still loading, "
                                                  "or an older mod build (it printed stats only to cyber_engine_tweaks/scripting.log)"))
        for role, stats_age, stats in stats_blocks:
            checks.append((INFO, "in-game stats", f"{role}, written {stats_age:.0f} s ago"))
            checks.extend(grade_stats(stats))
            append_history(history_path, {
                "time": datetime.datetime.now().isoformat(timespec="seconds"),
                "server_ping_ms": f"{server_ping:.0f}" if server_ping is not None else "",
                **{key: stats.get(key, "") for key in ("state", "sync", "role", "rtt_ms", "rtt_min", "rtt_max", "pps_in", "pps_out", "fps", "peer_rate", "missed_pct",
                                                    "missed_total_pct", "overwritten_pct", "out_merged_pct", "ignored", "age_ms", "avatar_err_m", "conflict", "peer_old")},
            })

        render(checks, events)
        if args.once:
            return 0 if not any(level == BAD for level, _, _ in checks) else 1
        time.sleep(REFRESH_SECONDS)


if __name__ == "__main__":
    try:
        sys.exit(run(parse_args()))
    except KeyboardInterrupt:
        print("\nmonitor stopped")
