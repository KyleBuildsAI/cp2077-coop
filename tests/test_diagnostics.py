"""Diagnostics: stats/event files, the live monitor, and what the panel reports.

D1  [STATS] goes to coop_stats_<role>.txt and every log line to a bounded
    coop_events.log; coop_monitor.py reads both (not CET's buffered log)
D2  a partner on an older build (no role bit) is not reported as a role
    conflict, only as an old version; two new hosts still conflict
D3  local menu / loading reads PAUSED (not LOST), and no false "old version"
    after a reload
D4  one long local frame (autosave, streaming) is not logged as STALE/LOST;
    a real silence still is
D5  missed% is split: packets overwritten in the DLL by a low local frame
    rate are not network loss; the sender's merged ticks are reported on its
    side; missed_pct is the last 5 s window, not a lifetime average
D6  the panel and the monitor grade with the same limits (RTT ~330 ms is
    normal on the LA - Warsaw - Russia route)
D7  avatar drift is measured from where the partner really is
D8  without the monitor, the panel names the real path coop-tools/coop_monitor.py
D9  monitor_status.txt left behind by a stopped monitor is shown as stale, not
    as a live ping; the monitor swaps the file in whole and removes it on Ctrl+C
D10 the history CSV locked by Excel gives a WARN, not a crash; a header change
    starts a new file (the D1/D10 runs write their history to a temp folder)

Usage: python test_diagnostics.py path/to/init.lua
"""
import math
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time

import test_two_players as harness
import test_timing as timing

SCRIPT = os.path.abspath(sys.argv[1])
# package root: init.lua lives in <root>/bin/x64/plugins/cyber_engine_tweaks/mods/CP2077Coop
PACKAGE = os.path.abspath(os.path.join(os.path.dirname(SCRIPT), *[os.pardir] * 6))
TOOLS = os.path.join(PACKAGE, "coop-tools")
sys.path.insert(0, TOOLS)
import coop_monitor as monitor  # noqa: E402

MOD_DIR = os.path.join("bin", "x64", "plugins", "cyber_engine_tweaks", "mods", "CP2077Coop")


def make_game_dir():
    """Throwaway game folder; the Lua instances run with the mod folder as cwd (like CET)."""
    game = tempfile.mkdtemp(prefix="coopdiag_")
    mod_dir = os.path.join(game, MOD_DIR)
    os.makedirs(mod_dir)
    os.makedirs(os.path.join(game, "red4ext", "plugins", "CP2077Coop"))
    with open(os.path.join(game, monitor.SERVER_INI), "w", encoding="utf-8") as handle:
        handle.write("server_ip=127.0.0.1\nserver_port=11778\n")
    return game, mod_dir


def upvalue(lua, name):
    lua.execute(timing.FIND_UPVALUE)
    return lua.eval("findUpvalue")(lua.globals().events["onUpdate"], name)


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def file_state(path):
    """(size, mtime) or None: tells whether a run touched the file."""
    try:
        status = os.stat(path)
    except FileNotFoundError:
        return None
    return status.st_size, status.st_mtime_ns


# ------------------------------------------------------------------ D1

def test_stats_and_events_files():
    game, mod_dir = make_game_dir()
    cwd = os.getcwd()
    try:
        os.chdir(mod_dir)
        host, joiner = harness.run_session(12.0)
        files = sorted(os.listdir(mod_dir))
        host_stats = read("coop_stats_host.txt")
        joiner_stats = read("coop_stats_joiner.txt")
        events = read("coop_events.log").splitlines()
        stamped = all(line[2] == ":" and line[5] == ":" and line[9:].startswith("[CP2077Coop]") for line in events)
        print(f"  files {files}; host stats {host_stats[:60]!r}...; {len(events)} event lines, time-stamped {stamped}")
        files_ok = (
            host_stats.count("[STATS]") == 1 and "role=host" in host_stats
            and joiner_stats.count("[STATS]") == 1 and "role=joiner" in joiner_stats
            and any("WORLD SYNC OK" in line for line in events)
            and not any("[STATS]" in line for line in events)
            and stamped
        )

        shown_events = monitor.recent_events(mod_dir)
        events_ok = any("WORLD SYNC OK" in line for line in shown_events) and not any("BOT" in line for line in shown_events)

        # bounded: a chatty session keeps only the newest lines
        diag = upvalue(joiner, "Diag")
        for index in range(1, 1001):
            diag.log(f"[CP2077Coop] filler {index}")
        bounded = read("coop_events.log").splitlines()
        bounded_ok = len(bounded) <= diag.EVENTS_MAX_LINES and bounded[-1].endswith("filler 1000")
        print(f"  after 1000 more lines: file has {len(bounded)} (max {diag.EVENTS_MAX_LINES}), last {bounded[-1][-20:]!r}")

        # monitor: one block per fresh stats file, stale files ignored, events from coop_events.log
        blocks = monitor.read_stats_files(mod_dir)
        old = time.time() - 60
        os.utime("coop_stats_joiner.txt", (old, old))
        fresh = monitor.read_stats_files(mod_dir)
        print(f"  monitor blocks {[b[0] for b in blocks]}, after joiner file aged 60 s {[b[0] for b in fresh]}, "
              f"{len(shown_events)} events shown, e.g. {shown_events[-1:]}")
        monitor_ok = (
            [b[0] for b in blocks] == ["host", "joiner"]
            and [b[0] for b in fresh] == ["host"]
            and blocks[0][2].get("state") == "OK"
            and events_ok
        )

        # end to end: the monitor script itself
        os.utime("coop_stats_joiner.txt", None)
        diag.log("[CP2077Coop] EVENT end-to-end marker")
        repo_history = os.path.join(TOOLS, monitor.HISTORY_FILE)
        repo_history_before = file_state(repo_history)
        test_history = os.path.join(game, "history.csv")
        run = subprocess.run([sys.executable, os.path.join(TOOLS, "coop_monitor.py"), "--once", "--no-geo", "--game", game,
                              "--history", test_history],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        out = run.stdout
        history_rows = read(test_history).splitlines() if os.path.exists(test_history) else []
        repo_untouched = file_state(repo_history) == repo_history_before
        end_to_end_ok = ("player round trip" in out and "in-game stats" in out and "end-to-end marker" in out and "none in the last" not in out
                         and len(history_rows) == 3 and repo_untouched)
        print(f"  monitor --once: exit {run.returncode}, rows shown: {'player round trip' in out}, events shown: {'recent events' in out}; "
              f"--history file has {len(history_rows)} lines (header + host + joiner), repo history untouched {repo_untouched}")
        if not end_to_end_ok:
            print(out[-1500:], run.stderr[-800:])
        return files_ok and bounded_ok and monitor_ok and end_to_end_ok
    finally:
        os.chdir(cwd)
        shutil.rmtree(game, ignore_errors=True)


# ------------------------------------------------------------------ D2

class OldPeer(timing.Peer):
    """A partner on an older mod build: v0.0.26 sends a unit forward vector
    (payload 0); v0.0.27 sends time/weather and flags but no role bit and no ping/pong."""

    def __init__(self, version, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.version = version

    def send_burst(self, t, pushes, peer):
        altered = []
        for x, y, z, fx, fy in pushes:
            length = math.hypot(fx, fy)
            payload = int(round(length - 1.0))
            kind, value = divmod(payload, 512)
            if self.version == "0.0.26":
                payload = 0
            elif kind in (1, 2):
                pass
            elif kind == 0:
                payload = value & ~256
            else:
                payload = 0
            altered.append((x, y, z, fx / length * (1.0 + payload), fy / length * (1.0 + payload)))
        super().send_burst(t, altered, peer)


def last_stats(lua):
    return harness.last_stats(lua)


def panel_texts(lua):
    lua.execute("""
        panelTexts = {}
        local colored = ImGui.TextColored
        ImGui.TextColored = function(r, g, b, a, s) panelTexts[#panelTexts + 1] = s return colored(r, g, b, a, s) end
    """)
    lua.globals().events["onDraw"]()
    texts = lua.globals().panelTexts
    return [texts[i] for i in range(1, len(texts) + 1)]


def test_old_partner_not_role_conflict():
    ok = True
    for version in ("0.0.26", "0.0.27"):
        rng = random.Random(4)
        host = OldPeer(version, "host", timing.make_peer("host", (100.0, 50.0)), 60.0, rng)
        joiner = timing.Peer("joiner", timing.make_peer("joiner", (-900.0, 400.0)), 60.0, rng)
        conflicts = []
        timing.run_pair(host, joiner, 11.0, lambda peer, t: conflicts.append(upvalue(joiner.lua, "Diag").roleConflict()) if peer is joiner else None)
        line = last_stats(joiner.lua)
        texts = panel_texts(joiner.lua)
        role_rows = [text for text in texts if "BOTH" in text or "unknown" in text]
        print(f"  joiner vs v{version} host: conflict ever {any(conflicts)}, last stats conflict={harness.stat(line, 'conflict')} "
              f"peer_old={harness.stat(line, 'peer_old')}; panel role rows {role_rows}")
        ok = ok and not any(conflicts) and harness.stat(line, "peer_old") == "true" and role_rows and "unknown" in role_rows[0]

    rng = random.Random(4)
    host = timing.Peer("host", timing.make_peer("host", (100.0, 50.0)), 60.0, rng)
    other = timing.Peer("host2", timing.make_peer("host", (-900.0, 400.0)), 60.0, rng)
    timing.run_pair(host, other, 6.0)
    both_hosts = harness.stat(last_stats(other.lua), "conflict")
    rng = random.Random(4)
    host = timing.Peer("host", timing.make_peer("host", (100.0, 50.0)), 60.0, rng)
    joiner = timing.Peer("joiner", timing.make_peer("joiner", (-900.0, 400.0)), 60.0, rng)
    timing.run_pair(host, joiner, 9.0)
    normal = last_stats(joiner.lua)
    print(f"  two new hosts: conflict={both_hosts}; new host + new joiner: conflict={harness.stat(normal, 'conflict')} "
          f"peer_old={harness.stat(normal, 'peer_old')}")
    return ok and both_hosts == "true" and harness.stat(normal, "conflict") == "false" and harness.stat(normal, "peer_old") == "false"


# ------------------------------------------------------------------ D3

def test_menu_is_paused_not_lost():
    rng = random.Random(6)
    host = timing.Peer("host", timing.make_peer("host", (100.0, 50.0)), 60.0, rng)
    joiner = timing.Peer("joiner", timing.make_peer("joiner", (-900.0, 400.0)), 60.0, rng)
    timing.run_pair(host, joiner, 8.0)
    mark = len(harness.logs(joiner.lua))
    joiner.g.preGame = True  # main menu / loading screen for 12 s, host keeps sending
    timing.run_pair(host, joiner, 20.0)
    during = harness.logs(joiner.lua)[mark:]
    mark = len(harness.logs(joiner.lua))
    joiner.g.preGame = False
    timing.run_pair(host, joiner, 27.0)
    after = harness.logs(joiner.lua)[mark:]
    paused_stats = [line for line in during if "[STATS]" in line]
    paused_ok = paused_stats and all(
        harness.stat(line, "state") == "PAUSED" and harness.stat(line, "sync") == "off"
        and harness.stat(line, "peer_old") == "false" and harness.stat(line, "avatar_err_m") == "-"
        for line in paused_stats
    )
    lost = [line for line in during + after if "-> LOST" in line or "-> STALE" in line]
    old_after = [line for line in after if "peer_old=true" in line]
    transitions = [line.split("EVENT connection ")[1] for line in during + after if "EVENT connection" in line]
    print(f"  menu: {len(paused_stats)} stats lines, all PAUSED/sync=off/peer_old=false: {bool(paused_ok)}; "
          f"transitions {transitions}; LOST/STALE events {lost}; peer_old=true after reload {len(old_after)}")
    graded = monitor.grade_stats(monitor.parse_stats(paused_stats[-1]))
    print(f"  monitor on a PAUSED line: {graded}")
    monitor_ok = len(graded) == 1 and graded[0][0] == monitor.INFO
    return paused_ok and not lost and not old_after and transitions[:1] == ["OK -> PAUSED"] and monitor_ok


# ------------------------------------------------------------------ D4

def test_long_frame_not_stale():
    rng = random.Random(9)
    host = timing.Peer("host", timing.make_peer("host", (100.0, 50.0)), 60.0, rng)
    joiner = timing.Peer("joiner", timing.make_peer("joiner", (-900.0, 400.0)), 60.0, rng,
                         hitches={8.0: 2.0, 14.0: 6.0})
    timing.run_pair(host, joiner, 24.0)
    hitch_events = [line for line in harness.logs(joiner.lua) if "EVENT connection" in line]
    stats = [line for line in harness.logs(joiner.lua) if "[STATS]" in line]
    stale_stats = [harness.stat(line, "state") for line in stats if harness.stat(line, "state") != "OK"]
    mark = len(harness.logs(joiner.lua))
    host.silent = True
    timing.run_pair(host, joiner, 31.0)
    silence_events = [line.split("connection ")[1] for line in harness.logs(joiner.lua)[mark:] if "EVENT connection" in line]
    print(f"  2 s and 6 s local frames: connection events {hitch_events}, non-OK stats {stale_stats}; "
          f"host silent 7 s: {silence_events}")
    return (not [line for line in hitch_events if "STALE" in line or "LOST" in line]
            and not stale_stats and silence_events == ["OK -> STALE", "STALE -> LOST"])


# ------------------------------------------------------------------ D5

def loss_run(host_fps, joiner_fps, phases, seed=11):
    """phases: [(until_seconds, loss)] applied in order; returns (host, joiner) peers."""
    rng = random.Random(seed)
    host = timing.Peer("host", timing.make_peer("host", (100.0, 50.0)), host_fps, rng)
    joiner = timing.Peer("joiner", timing.make_peer("joiner", (-900.0, 400.0)), joiner_fps, rng)
    saved = timing.LOSS
    try:
        for until, loss in phases:
            timing.LOSS = loss
            timing.run_pair(host, joiner, until)
    finally:
        timing.LOSS = saved
    return host, joiner


def window_stats(lua, skip=1):
    return [line for line in harness.logs(lua) if "[STATS]" in line][skip:]


def stat_values(lines, key):
    return [float(harness.stat(line, key)) for line in lines if harness.stat(line, key) not in (None, "-")]


def test_missed_split():
    results = []
    # A: receiver at 25 fps, lossless link: DLL overwrites, not loss
    host, joiner = loss_run(60.0, 25.0, [(25.0, 0.0)])
    lines = window_stats(joiner.lua)
    missed, over = stat_values(lines, "missed_pct"), stat_values(lines, "overwritten_pct")
    fps = stat_values(lines, "fps")
    a_ok = max(missed) < 3.0 and min(over) > 10.0 and 23 <= fps[-1] <= 27
    print(f"  A joiner 25 fps, 0% loss: missed {max(missed):.1f}% max, overwritten {min(over):.1f}-{max(over):.1f}%, fps {fps[-1]:.0f}")
    graded = {name: level for level, name, _ in monitor.grade_stats(monitor.parse_stats(lines[-1]))}
    print(f"  monitor on that line: packets {graded.get('packets in / out')!r}, missed {graded.get('missed packets')!r}, "
          f"frame rate {graded.get('frame rate')!r}")
    a_ok = a_ok and graded.get("packets in / out") == monitor.GOOD and graded.get("missed packets") == monitor.GOOD         and graded.get("frame rate") == monitor.WARN
    results.append(a_ok)

    # B: 60/60 fps, 10% real loss: reported as missed
    host, joiner = loss_run(60.0, 60.0, [(25.0, 0.10)])
    missed = stat_values(window_stats(joiner.lua), "missed_pct")
    b_ok = 6.0 <= sum(missed) / len(missed) <= 14.0
    print(f"  B 60/60 fps, 10% loss: missed avg {sum(missed) / len(missed):.1f}%")
    results.append(b_ok)

    # C: sender at 25 fps: its DLL merges ticks; it says so, the receiver counts them as missed
    host, joiner = loss_run(25.0, 60.0, [(25.0, 0.0)])
    merged = stat_values(window_stats(host.lua), "out_merged_pct")
    missed = stat_values(window_stats(joiner.lua), "missed_pct")
    c_ok = 12.0 <= merged[-1] <= 21.0 and abs(missed[-1] - merged[-1]) < 5.0
    print(f"  C host 25 fps: host out_merged {merged[-1]:.1f}%, joiner missed {missed[-1]:.1f}%")
    results.append(c_ok)

    # D: 30 s clean, then 10 s at 30% loss: the window shows it, the session total lags
    host, joiner = loss_run(60.0, 60.0, [(30.0, 0.0), (41.0, 0.30)])
    last = window_stats(joiner.lua)[-1]
    window, total = float(harness.stat(last, "missed_pct")), float(harness.stat(last, "missed_total_pct"))
    d_ok = window >= 20.0 and total < window - 8.0
    print(f"  D 30 s clean then 30% loss: last window {window:.1f}%, session {total:.1f}%")
    results.append(d_ok)

    # local relay log: per-client DLL skips and link drops over ~30 s
    relay_dir = tempfile.mkdtemp(prefix="cooprelay_")
    try:
        with open(os.path.join(relay_dir, "coop_relay.log"), "w", encoding="utf-8") as handle:
            for block in range(8):
                handle.write(f"00:00:{block * 5:02d} [STATS] clients=2 link: +115ms +-20ms loss 1.0%\n")
                handle.write(f"00:00:{block * 5:02d} [STATS]   #1 127.0.0.1:5000        in= 30.0/s total={block * 141} "
                             f"gaps={block * 9} late=0 fwd={block * 139} dropped={block * 2} idle=0.0s\n")
                handle.write(f"00:00:{block * 5:02d} [STATS]   #2 127.0.0.1:5001        in= 30.0/s total={block * 150} "
                             f"gaps=0 late=0 fwd={block * 150} dropped=0 idle=0.0s\n")
        relay = monitor.check_relay_log(relay_dir)
    finally:
        shutil.rmtree(relay_dir, ignore_errors=True)
    print(f"  relay log: {[detail for _, _, detail in relay]}")
    results.append(len(relay) == 2 and "skipped 6.0 %" in relay[0][2] and "dropped 1.4 %" in relay[0][2]
                   and "skipped 0.0 %" in relay[1][2])

    # panel shows the split rows
    texts = panel_texts(joiner.lua)
    labels = [text for text in texts if "read/s" in text or "merged" in text or "net loss" in text or "local, not the network" in text]
    print(f"  panel: {labels}")
    results.append(len(labels) == 4)
    return all(results)


# ------------------------------------------------------------------ D6

def lua_limits(diag):
    limits = {}
    for key, pair in diag.LIMITS.items():
        limits[key] = (pair[1], pair[2])
    return limits


def test_panel_and_monitor_grade_alike():
    lua = timing.make_peer("joiner", (0.0, 0.0))
    diag = upvalue(lua, "Diag")
    limits = lua_limits(diag)
    mismatched = {key: (pair, monitor.EXPECT.get(key)) for key, pair in limits.items()
                  if tuple(float(v) for v in pair) != tuple(float(v) for v in monitor.EXPECT.get(key, ()))}
    levels = {}
    for rtt in (330.0, 500.0, 650.0, 800.0):
        panel = diag.levelFor(rtt, diag.LIMITS.rtt_ms)
        tool = monitor.grade(rtt, monitor.EXPECT["rtt_ms"])
        levels[rtt] = (panel, tool.strip())
    print(f"  limits shared with the monitor: {sorted(limits)}; mismatched {mismatched}; RTT panel/monitor {levels}")
    same = all({"good": "OK", "warn": "WARN", "bad": "BAD"}[panel] == tool for panel, tool in levels.values())
    return not mismatched and same and levels[330.0][0] == "good" and set(limits) >= {"rtt_ms", "server_ping_ms", "missed_pct", "age_ms"}


# ------------------------------------------------------------------ D7

def test_drift_against_real_position():
    rng = random.Random(12)
    host = timing.Peer("host", timing.make_peer("host", (100.0, 50.0)), 60.0, rng, path=timing.straight)
    joiner = timing.Peer("joiner", timing.make_peer("joiner", (-900.0, 400.0)), 60.0, rng)
    diag = upvalue(joiner.lua, "Diag")
    samples = {"Run": [], "Sprint": []}

    def on_frame(peer, t):
        npc = joiner.g.npc
        if peer is not joiner or npc is None or diag.avatarError is None:
            return
        for gait, (begin, end, _) in timing.STEADY.items():
            if begin <= t <= end:
                true_x, true_y = timing.straight(t)
                truth = math.hypot(npc.x - true_x, npc.y - true_y)
                samples[gait].append((diag.avatarError, truth))

    timing.run_pair(host, joiner, 16.0, on_frame)
    ok = True
    for gait, pairs in samples.items():
        reading = sum(r for r, _ in pairs) / len(pairs)
        truth = sum(t for _, t in pairs) / len(pairs)
        print(f"  {gait}: panel drift avg {reading:.2f} m, real distance to the partner avg {truth:.2f} m ({len(pairs)} frames)")
        ok = ok and abs(reading - truth) < 0.35
    graded = {name: detail for _, name, detail in monitor.grade_stats(monitor.parse_stats(harness.last_stats(joiner.lua)))}
    print(f"  monitor: {graded.get('avatar drift')}")
    return ok and "avg" in graded.get("avatar drift", "")


# ------------------------------------------------------------------ D8

def test_panel_names_monitor_path():
    lua = timing.make_peer("joiner", (0.0, 0.0))
    texts = panel_texts(lua)
    relay_rows = [text for text in texts if "coop_monitor" in text]
    source = open(SCRIPT, encoding="utf-8").read()
    wrong = [line.strip() for line in source.splitlines() if re.search(r"(?<!coop-)tools/coop_monitor", line)]
    print(f"  panel without a monitor: {relay_rows}; wrong paths left in init.lua: {wrong}")
    script_exists = os.path.isfile(os.path.join(TOOLS, "coop_monitor.py"))
    return relay_rows == ["run: python coop-tools/coop_monitor.py"] and not wrong and script_exists


# ------------------------------------------------------------------ D9

LEVEL_COLORS = {(0.4, 0.9, 0.45): "good", (1.0, 0.8, 0.25): "warn", (1.0, 0.35, 0.35): "bad", (0.8, 0.8, 0.8): "neutral"}


def panel_rows(lua):
    """{label: (value, level)} of the panel; Diag.row draws ImGui.Text(label), then TextColored(value)."""
    lua.execute("""
        panelRows = {}
        local lastLabel = ""
        ImGui.Text = function(s) lastLabel = s end
        ImGui.TextColored = function(r, g, b, a, s) panelRows[#panelRows + 1] = { lastLabel, s, r, g, b } end
    """)
    lua.globals().events["onDraw"]()
    rows = lua.globals().panelRows
    result = {}
    for index in range(1, len(rows) + 1):
        label, value, red, green, blue = (rows[index][key] for key in range(1, 6))
        result[label] = (value, LEVEL_COLORS.get((round(red, 2), round(green, 2), round(blue, 2)), "?"))
    return result


def test_monitor_status_staleness():
    lua = timing.make_peer("joiner", (0.0, 0.0))
    diag = upvalue(lua, "Diag")
    now = int(time.time())
    status = "server=203.0.113.7:11778\nserver_ping_ms=186\nserver_location=Warsaw, Poland (OVH)\n"

    def panel_with(text):
        if text is None:
            os.remove(diag.MONITOR_FILE)
        else:
            with open(diag.MONITOR_FILE, "w", encoding="utf-8") as handle:
                handle.write(text)
        diag.readMonitorStatus()
        rows = panel_rows(lua)
        return {label: rows[label] for label in ("Relay server", "Your ping to relay", "Relay location") if label in rows}

    fresh = panel_with(status + f"updated={now - 3}\n")
    old = panel_with(status + f"updated={now - 120}\n")
    unstamped = panel_with(status)
    gone = panel_with(None)
    for name, rows in (("fresh", fresh), ("2 min old", old), ("no 'updated'", unstamped), ("no file", gone)):
        print(f"  panel, monitor file {name}: {rows}")
    lua_ok = (
        fresh.get("Relay server") == ("203.0.113.7:11778", "neutral")
        and fresh.get("Your ping to relay") == ("186 ms", "good")
        and re.fullmatch(r"203\.0\.113\.7:11778 \(monitor stopped 12[01] s ago\)", old["Relay server"][0])
        and old["Relay server"][1] == "warn" and "Your ping to relay" not in old and "Relay location" in old
        and unstamped.get("Relay server") == ("203.0.113.7:11778 (monitor not running)", "warn")
        and "Your ping to relay" not in unstamped
        and gone == {"Relay server": ("run: python coop-tools/coop_monitor.py", "warn")}
    )

    # monitor side: swapped in whole; written in place while the game holds the file open
    game, mod_dir = make_game_dir()
    try:
        monitor.write_panel_status(mod_dir, "203.0.113.7:11778", 186.0, None)
        path = os.path.join(mod_dir, monitor.PANEL_STATUS_FILE)
        written = read(path)
        with open(path, encoding="utf-8") as held:  # like CET's io.open: shares read/write, not delete
            monitor.write_panel_status(mod_dir, "203.0.113.7:11778", 190.0, None)
            held_text = held.read()
        while_held = read(path)
        leftovers = [name for name in os.listdir(mod_dir) if name.endswith(".tmp")]
        print(f"  monitor wrote {written.splitlines()}; while the game held the file: {while_held.splitlines()[-1]!r} "
              f"(reader saw {len(held_text)} bytes), temp files left {leftovers}")
        monitor_ok = ("updated=" in written and "server_ping_ms=186" in written and "server_ping_ms=190" in while_held
                      and not leftovers)

        # Ctrl+C: the status file goes, so the panel says at once that the monitor stopped
        seen = {}

        def interrupt(checks, events):
            seen["status written"] = os.path.exists(path)
            raise KeyboardInterrupt

        saved = (monitor.render, monitor.ping_ms, monitor.append_history, sys.argv)
        monitor.render, monitor.ping_ms = interrupt, lambda host: 41.0
        monitor.append_history = lambda history_path, row: None
        sys.argv = ["coop_monitor.py", "--game", game, "--no-geo"]
        try:
            code = monitor.run(monitor.parse_args())
        finally:
            monitor.render, monitor.ping_ms, monitor.append_history, sys.argv = saved
        print(f"  Ctrl+C: exit {code}, status written before {seen.get('status written')}, left after {os.path.exists(path)}")
        stop_ok = code == 0 and seen.get("status written") and not os.path.exists(path)
    finally:
        shutil.rmtree(game, ignore_errors=True)
    return bool(lua_ok and monitor_ok and stop_ok)


# ------------------------------------------------------------------ D10

class ExcelLock:
    """Holds a file the way Excel holds an open .csv: others may read it, nobody may write it."""

    def __init__(self, path):
        self.path = path
        self.handle = None
        self.mode = None

    def __enter__(self):
        if os.name != "nt":
            self.mode = os.stat(self.path).st_mode
            os.chmod(self.path, 0o444)
            return self
        import ctypes
        from ctypes import wintypes
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel32.CreateFileW.restype = wintypes.HANDLE
        self.kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                              wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        generic_read, file_share_read, open_existing = 0x80000000, 0x1, 3
        handle = self.kernel32.CreateFileW(self.path, generic_read, file_share_read, None, open_existing, 0, None)
        if handle in (None, ctypes.c_void_p(-1).value):
            raise ctypes.WinError(ctypes.get_last_error())
        self.handle = handle
        return self

    def __exit__(self, *exc_info):
        if self.handle is not None:
            self.kernel32.CloseHandle(self.handle)
        else:
            os.chmod(self.path, self.mode)


STATS_LINE = ("[CP2077Coop] [STATS] state=OK sync=on role=host rtt_ms=330 rtt_min=310 rtt_max=350 rtt_n=12 pps_in=29.8 "
              "pps_out=30.0 fps=60 peer_rate=30.0 missed_pct=1.0 missed_total_pct=1.0 overwritten_pct=0.0 out_merged_pct=0.0 "
              "ignored=0 age_ms=40 avatar_err_m=0.30 drift_avg_m=0.30 drift_max_m=0.50 conflict=false peer_old=false\n")


def test_history_locked_by_excel():
    game, mod_dir = make_game_dir()
    try:
        history = os.path.join(game, "history.csv")
        row = {"time": "2026-10-04T12:00:00", "rtt_ms": "330"}
        first = monitor.append_history(history, row)
        with ExcelLock(history):
            locked = monitor.append_history(history, row)
            with open(os.path.join(mod_dir, "coop_stats_host.txt"), "w", encoding="utf-8") as handle:
                handle.write(STATS_LINE)
            run = subprocess.run([sys.executable, os.path.join(TOOLS, "coop_monitor.py"), "--once", "--no-geo", "--game", game,
                                  "--history", history],
                                 capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        after = monitor.append_history(history, row)
        lines = read(history).splitlines()
        shown = [line.strip() for line in run.stdout.splitlines() if "history" in line]
        print(f"  locked: append returned {locked!r}; monitor --once exit {run.returncode}, "
              f"traceback {'Traceback' in run.stderr}, rows {shown}")
        print(f"  unlocked again: append returned {after!r}, file has {len(lines)} lines {lines}")
        locked_ok = (first is None and locked and "Excel" in locked and after is None
                     and lines == ["time,rtt_ms", "2026-10-04T12:00:00,330", "2026-10-04T12:00:00,330"]
                     and run.returncode in (0, 1) and "Traceback" not in run.stderr
                     and any("WARN" in line and "close history.csv in Excel" in line for line in shown))

        # columns changed (newer monitor): old rows kept in a dated file, never under the wrong header
        with open(history, "w", encoding="utf-8") as handle:
            handle.write("time,server_ping_ms\n2026-10-03T20:00:00,186\n")
        with ExcelLock(history):
            refused = monitor.append_history(history, row)
        renamed = monitor.append_history(history, row)
        kept = [name for name in os.listdir(game) if name.startswith("history-until-") and name.endswith(".csv")]
        old_rows = read(os.path.join(game, kept[0])).splitlines() if kept else []
        print(f"  new columns: while locked {refused!r}; then old file kept as {kept} {old_rows}, "
              f"new file {read(history).splitlines()}")
        header_ok = (refused and "Excel" in refused and renamed is None and len(kept) == 1
                     and old_rows == ["time,server_ping_ms", "2026-10-03T20:00:00,186"]
                     and read(history).splitlines() == ["time,rtt_ms", "2026-10-04T12:00:00,330"])
        return bool(locked_ok and header_ok)
    finally:
        shutil.rmtree(game, ignore_errors=True)


if __name__ == "__main__":
    tests = {
        "D1 stats/events go to their own flushed files; the monitor reads them": test_stats_and_events_files,
        "D2 older partner: no false role conflict, reported as old version": test_old_partner_not_role_conflict,
        "D3 menu / loading reads PAUSED, no false LOST or old-version after reload": test_menu_is_paused_not_lost,
        "D4 a long local frame is not logged as STALE/LOST; real silence is": test_long_frame_not_stale,
        "D5 missed split from local overwrites and sender merges, windowed": test_missed_split,
        "D6 panel and monitor grade RTT, ping, missed, age, drift alike": test_panel_and_monitor_grade_alike,
        "D7 avatar drift is measured from where the partner really is": test_drift_against_real_position,
        "D8 panel names the real monitor path (coop-tools/)": test_panel_names_monitor_path,
        "D9 panel drops relay values once the monitor stops; status file swapped in whole": test_monitor_status_staleness,
        "D10 history CSV open in Excel: monitor warns and keeps running; new columns start a new file": test_history_locked_by_excel,
    }
    results = {}
    for name, test in tests.items():
        print(f"-- {name}")
        try:
            results[name] = bool(test())
        except Exception as error:  # report and keep going
            print(f"  EXCEPTION {error!r}")
            results[name] = False
    for name, passed in results.items():
        print(f"{'PASS' if passed else 'FAIL'}  {name}")
    sys.exit(0 if all(results.values()) else 1)
