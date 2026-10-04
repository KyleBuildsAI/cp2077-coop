"""Payload schedule (Total Sync Plan phase 0): what each packet carries, per role.

The DLL carries one 9-bit payload per packet in the length of the forward
vector (flags, time, weather, ping, pong, vehicle index, mod fingerprint).
Two real init.lua instances, host and joiner, run at 60 fps through the
test_timing harness (DLL model, 115 ms + 0..20 ms, 1% loss). Every
Sync.buildPayload call is one slot: the payload that every push of that
frame carries.

P1  10,000 slots per role, short mod lists (8 / 7): player flags in >= 85% of
    slots for both roles, time and weather 1 +- 0.1 Hz (host), ping 1 +- 0.1 Hz,
    every mod hash sent at least twice in the first 10 s, at most 2 non-flag
    slots in a row, a flag change out within 2 slots, the panel's comparison exact
P2  time and weather changes go out at once (within 0.15 s)
P3  long mod lists (30 / 28, as test_mods): the burst ends, flags >= 85% after it,
    the panel's comparison exact (the share over the whole run is printed: the
    one-time burst counts against it)
P4  vehicle index: only after a flags slot that says "in a vehicle", within
    3 slots of it, once more 0.2 s later, then at least 0.5 Hz while mounted
    (time, weather, index and ping share the non-flag budget), none after
    leaving; flags in >= 85% of the slots while mounted
P5  partner reloads (6 s silent) and resets without a pause: the other side sends
    its list again, the reloaded side compares again, the panel ends exact, and
    both go back to one pair every 10 s (no endless re-send)
P7  host at 20 fps (20 slots/s), on foot and mounted: player flags still in >= 85%
    of the slots after the mod burst (at most EXTRA_SHARE 14.5% non-flag slots),
    ping >= 0.75 Hz, time and weather (and the index) repeat at >= 0.5 Hz on foot,
    >= 0.3 Hz mounted, never more than 6 s between two time packets, and a time
    change still goes out within 0.2 s
P6  car entry with no random loss: the joiner shows the host's car within 0.3 s,
    also when the first "in a vehicle" flags packet is lost, within 0.5 s when the
    first vehicle index packet is lost (sent again after 0.2 s) or the joiner runs
    at 24 / 27 fps (the DLL keeps only the newest packet), and never hides it on
    the way

"Mods compared" is logged after two received cycles from the union of both. A
pair lost in both (about 1 run in 24 here, far more at the live 16% loss) makes
that line wrong while later cycles fix the panel, so the checks read the panel
(Mods.compare) and print the first line.

Usage: python test_payload_schedule.py path/to/init.lua
"""
import collections
import os
import random
import sys

import test_live_bugs as live
import test_timing as timing

TYPE_STRIDE = 512
FLAGS, TIME, WEATHER, PING, PONG, VEHICLE, MOD_HI, MOD_LO = range(8)
NAMES = {FLAGS: "flags", TIME: "time", WEATHER: "weather", PING: "ping", PONG: "pong",
         VEHICLE: "vehicle", MOD_HI: "mod hi", MOD_LO: "mod lo"}
SLOTS = 10_000
FLAG_SHARE_MIN = 85.0
CROUCH = 1
IN_VEHICLE = 16
VEHICLE_INDEX = 35

SHARED = [f"archive/shared_mod_{i}" for i in range(6)]
HOST_ONLY = ["cet/HostOnlyA", "redscript/HostOnlyB"]
JOINER_ONLY = ["archive/JoinerOnlyA"]
LONG_SHARED = [f"archive/shared_mod_{i}" for i in range(25)]
LONG_HOST_ONLY = ["cet/HostOnlyA", "redscript/HostOnlyB", "red4ext/HostOnlyC", "archive/HostOnlyD", "tweak/HostOnlyE"]
LONG_JOINER_ONLY = ["cet/JoinerOnlyA", "archive/JoinerOnlyB", "redmod/JoinerOnlyC"]

# every Sync.buildPayload call: Sync.clock, the frame's sim time, payload, mod burst on
RECORD = r"""
function recordSlots(Sync, Mods)
    slotClock, slotTime, slotPayload, slotBurst = {}, {}, {}, {}
    local build = Sync.buildPayload
    Sync.buildPayload = function(player, isHost)
        local payload = build(player, isHost)
        local n = #slotPayload + 1
        slotClock[n] = Sync.clock
        slotTime[n] = simTime
        slotPayload[n] = payload
        slotBurst[n] = Mods.bursting ~= nil and Mods.bursting()  -- builds before 0.0.32: none
        return payload
    end
end
function findIn(fn, wanted)
    local index = 1
    while true do
        local name, value = debug.getupvalue(fn, index)
        if name == nil then return nil end
        if name == wanted then return value end
        index = index + 1
    end
end
"""

# the host's clock runs at the game's 8x speed (3 game minutes = 22.5 s); weather is a global
WORLD_MOCK = r"""
weatherIndex = 1
function player:CP2077Coop_GetTimeOfDayMinutes() return 600 + simTime * 8 / 60 end
function player:CP2077Coop_GetWeatherIndex() return weatherIndex end
function player:CP2077Coop_GetMountedVehicleIndex() return VEHICLE_INDEX end
"""


def write_modlist(names):
    with open("modlist.txt", "w", encoding="utf-8") as handle:
        handle.write("".join(name + "\n" for name in names))


class Side:
    """One instance plus its recorded slots."""

    def __init__(self, role, position, mods, rng):
        write_modlist(mods)
        lua = timing.make_peer(role, position)
        os.remove("modlist.txt")
        lua.execute(RECORD + WORLD_MOCK.replace("VEHICLE_INDEX", str(VEHICLE_INDEX)))
        self.sync = timing.upvalue(lua, "Sync")
        self.mods = lua.eval("findIn")(self.sync.receivePayload, "Mods")
        lua.eval("recordSlots")(self.sync, self.mods)
        self.role = role
        self.lua = lua
        self.peer = timing.Peer(role, lua, 60.0, rng)
        self.flag_changes = []  # (sim time, flags) set by the test
        self.stamped = []  # (sim time, line) for every log line
        frame = self.peer.frame

        def stamped_frame(t, other):
            frame(t, other)
            logs = lua.globals().logs
            for index in range(len(self.stamped) + 1, len(logs) + 1):
                self.stamped.append((t, logs[index]))

        self.peer.frame = stamped_frame

    def slots(self):
        g = self.lua.globals()
        count = len(g.slotPayload)
        return [(g.slotClock[i], g.slotTime[i], int(g.slotPayload[i]), bool(g.slotBurst[i]))
                for i in range(1, count + 1)]

    def hashes(self):
        table = self.mods.hashes
        return [int(table[i]) for i in range(1, len(table) + 1)]

    def logs(self):
        return timing.logs(self.lua)

    def set_flags(self, t, flags):
        self.lua.globals().localFlags = flags
        self.flag_changes.append((t, flags))


def make_pair(host_mods, joiner_mods, seed):
    rng = random.Random(seed)
    host = Side("host", (100.0, 50.0), host_mods, rng)
    joiner = Side("joiner", (-900.0, 400.0), joiner_mods, rng)
    return host, joiner


def kind(payload):
    return payload // TYPE_STRIDE


def rate(times):
    """Events per second between the first and the last event."""
    if len(times) < 2:
        return 0.0
    return (len(times) - 1) / (times[-1] - times[0])


def mod_pairs(slots):
    """(sim time of the LO, hash) for every HI+LO pair the slots carry."""
    pairs, high = [], None
    for _, t, payload, _ in slots:
        if kind(payload) == MOD_HI:
            high = payload % 256
        elif kind(payload) == MOD_LO and high is not None:
            pairs.append((t, high * 256 + payload % 256))
            high = None
    return pairs


def longest_extra_run(slots):
    run = best = 0
    for _, _, payload, _ in slots:
        run = run + 1 if kind(payload) != FLAGS else 0
        best = max(best, run)
    return best


def flag_delays(side, slots):
    """Slots from each flag change until the first flags payload carrying it. The test sets
    the flags after a frame, so the game reads them from the next frame on. Changes in the
    last second of `slots` are left out (the slot that carries them may lie beyond)."""
    delays = []
    for change_t, flags in side.flag_changes:
        if change_t > slots[-1][1] - 1.0:
            continue
        after = [s for s in slots if s[1] > change_t]
        for index, (_, _, payload, _) in enumerate(after):
            if kind(payload) == FLAGS and payload % 256 == flags:
                delays.append(index)
                break
        else:
            delays.append(None)
    return delays


def comparison(side):
    return next((line.split("EVENT ", 1)[1] for line in side.logs() if "mods compared" in line), "")


def panel_comparison(side):
    """What the panel and STATS show now: you, partner, shared, only you, only partner."""
    shared, only_mine, only_partner, remote = side.lua.eval("function(Mods) return Mods.compare() end")(side.mods)
    if shared is None:
        return None
    return len(side.hashes()), int(remote), int(shared), len(only_mine), int(only_partner)


def comparisons(side):
    return [line.split("EVENT ", 1)[1] for line in side.logs() if "mods compared" in line]


def share(slots, wanted=FLAGS):
    return 100.0 * sum(1 for s in slots if kind(s[2]) == wanted) / len(slots) if slots else 0.0


def describe(slots):
    counts = collections.Counter(kind(s[2]) for s in slots)
    return ", ".join(f"{NAMES.get(k, k)} {counts[k]}" for k in sorted(counts))


# ------------------------------------------------------------------ P1 + P2

def crouch_toggler(side, period, start):
    """Crouch on and off every `period` s from `start`, so the flags change between slots."""
    def on_frame(t):
        if t < start:
            return
        wanted = CROUCH if int((t - start) / period) % 2 == 0 else 0
        if int(side.lua.globals().localFlags) != wanted:
            side.set_flags(t, wanted)
    return on_frame


def run_main():
    host, joiner = make_pair(SHARED + HOST_ONLY, SHARED + JOINER_ONLY, seed=31)
    host_crouch = crouch_toggler(host, 1.7, 20.0)
    joiner_crouch = crouch_toggler(joiner, 2.3, 20.0)
    weather_at = {100.0: 4, 200.0: 6}

    def on_frame(peer, t):
        if peer is host.peer:
            host_crouch(t)
            for at, index in list(weather_at.items()):
                if t >= at:
                    host.lua.globals().weatherIndex = index
                    del weather_at[at]
        else:
            joiner_crouch(t)

    seconds = SLOTS / 30.0 + 2.0
    timing.run_pair(host.peer, joiner.peer, seconds, on_frame)
    return host, joiner


def test_main_run(host, joiner):
    ok = True
    for side, own, other in ((host, SHARED + HOST_ONLY, SHARED + JOINER_ONLY),
                             (joiner, SHARED + JOINER_ONLY, SHARED + HOST_ONLY)):
        slots = side.slots()
        if len(slots) < SLOTS:
            print(f"  {side.role}: only {len(slots)} slots")
            return False
        slots = slots[:SLOTS]
        flags = share(slots)
        span = slots[-1][1] - slots[0][1]
        times = [s[1] for s in slots if kind(s[2]) == TIME]
        weathers = [s[1] for s in slots if kind(s[2]) == WEATHER]
        pings = [s[1] for s in slots if kind(s[2]) == PING]
        burst_end = max((s[1] for s in slots if s[3]), default=0.0)
        early = collections.Counter(h for t, h in mod_pairs(slots) if t < 10.0)
        hashes = side.hashes()
        fewest = min(early.get(h, 0) for h in hashes)
        run = longest_extra_run(slots)
        delays = flag_delays(side, slots)
        expected = (len(own), len(other), len(SHARED), len(own) - len(SHARED), len(other) - len(SHARED))
        compared = comparison(side)
        panel = panel_comparison(side)
        print(f"  {side.role:6} {len(slots)} slots in {span:.1f} s: flags {flags:.2f} %, time {rate(times):.3f} Hz, "
              f"weather {rate(weathers):.3f} Hz, ping {rate(pings):.3f} Hz; mod burst until {burst_end:.1f} s")
        print(f"         {describe(slots)}")
        print(f"         each of {len(hashes)} mod hashes sent >= {fewest}x in the first 10 s; longest non-flag run "
              f"{run}; flag change -> flags slot within {max(d for d in delays if d is not None) if delays else '-'} "
              f"slots ({len(delays)} changes, missing {delays.count(None)})")
        print(f"         panel at the end (you, partner, shared, only you, only partner): {panel}, want {expected}; "
              f"first event (two cycles): {compared}")
        world_ok = (abs(rate(times) - 1.0) <= 0.1 and abs(rate(weathers) - 1.0) <= 0.1) if side is host \
            else (not times and not weathers)
        ok = ok and (
            flags >= FLAG_SHARE_MIN
            and world_ok
            and abs(rate(pings) - 1.0) <= 0.1
            and fewest >= 2
            and run <= 2
            and delays and None not in delays and max(delays) <= 2
            and panel == expected
        )
    return ok


def test_world_changes_at_once(host):
    slots = host.slots()
    delays = []
    # 600 + t * 8/60 game minutes: the 3-minute value steps every 22.5 s
    step = 22.5
    for k in range(1, int(slots[-1][1] / step)):
        change_t = k * step
        value = (600 + 3 * k) // 3
        first = next((s[1] for s in slots if kind(s[2]) == TIME and s[2] % TYPE_STRIDE == value), None)
        delays.append(("time", change_t, None if first is None else first - change_t))
    for change_t, index in ((100.0, 4), (200.0, 6)):
        first = next((s[1] for s in slots if s[1] >= change_t and kind(s[2]) == WEATHER
                      and s[2] % TYPE_STRIDE == index + 1), None)
        delays.append(("weather", change_t, None if first is None else first - change_t))
    worst = max((d for _, _, d in delays if d is not None), default=None)
    missing = [(name, at) for name, at, d in delays if d is None]
    print(f"  {len(delays)} changes (time every 22.5 s, weather at 100 and 200 s): slowest out after "
          f"{worst:.3f} s, missing {missing}")
    return not missing and worst is not None and worst <= 0.15


def test_long_lists():
    host, joiner = make_pair(LONG_SHARED + LONG_HOST_ONLY, LONG_SHARED + LONG_JOINER_ONLY, seed=32)
    timing.run_pair(host.peer, joiner.peer, 150.0)
    ok = True
    for side, own, other in ((host, LONG_SHARED + LONG_HOST_ONLY, LONG_SHARED + LONG_JOINER_ONLY),
                             (joiner, LONG_SHARED + LONG_JOINER_ONLY, LONG_SHARED + LONG_HOST_ONLY)):
        slots = side.slots()
        burst_end = max((s[1] for s in slots if s[3]), default=0.0)
        after = [s for s in slots if s[1] > burst_end + 1.0]
        expected = (len(own), len(other), len(LONG_SHARED), len(own) - len(LONG_SHARED), len(other) - len(LONG_SHARED))
        panel = panel_comparison(side)
        print(f"  {side.role:6} {len(own)} mods: burst until {burst_end:.1f} s, flags after it {share(after):.2f} % "
              f"({len(after)} slots), whole 150 s {share(slots):.2f} %; panel {panel}, want {expected}; "
              f"first event: {comparison(side)}")
        ok = ok and burst_end < 40.0 and share(after) >= FLAG_SHARE_MIN and panel == expected
    return ok


# ------------------------------------------------------------------ P4

def test_vehicle_index():
    host, joiner = make_pair(SHARED + HOST_ONLY, SHARED + JOINER_ONLY, seed=33)
    seen = set()
    mount, leave = 20.0, 40.0

    def on_frame(peer, t):
        if peer is host.peer:
            g = host.lua.globals()
            if mount <= t < leave and g.localFlags != IN_VEHICLE:
                host.set_flags(t, IN_VEHICLE)
            elif t >= leave and g.localFlags == IN_VEHICLE:
                host.set_flags(t, 0)
        elif joiner.sync.remoteVehicleIndex is not None:
            seen.add(int(joiner.sync.remoteVehicleIndex))

    timing.run_pair(host.peer, joiner.peer, 50.0, on_frame)
    slots = host.slots()
    first_flag = next(i for i, s in enumerate(slots) if kind(s[2]) == FLAGS and s[2] & IN_VEHICLE)
    vehicle = [i for i, s in enumerate(slots) if kind(s[2]) == VEHICLE]
    mounted = [s for s in slots if mount + 0.1 <= s[1] < leave]
    times = [slots[i][1] for i in vehicle]
    resend = times[1] - times[0] if len(times) > 1 else None
    late = [t for t in times if t >= leave + 0.1]
    wrong = {slots[i][2] % TYPE_STRIDE for i in vehicle} - {VEHICLE_INDEX}
    print(f"  mounted {mount:.0f}-{leave:.0f} s: first 'in a vehicle' flags slot #{first_flag} at "
          f"{slots[first_flag][1]:.2f} s, first index slot #{vehicle[0] if vehicle else '-'}, sent again after "
          f"{'-' if resend is None else f'{resend:.2f} s'}; index rate "
          f"{rate(times):.3f} Hz, after leaving {len(late)}, wrong index {wrong}; flags while mounted "
          f"{share(mounted):.1f} %; joiner saw index {sorted(seen)}")
    return (bool(vehicle) and first_flag < vehicle[0] <= first_flag + 3
            and resend is not None and 0.15 <= resend <= 0.35
            and rate(times[1:]) >= 0.5 and share(mounted) >= FLAG_SHARE_MIN and not late and not wrong and seen == {VEHICLE_INDEX})


# ------------------------------------------------------------------ P6

CAR_MOUNT = 12.0
CAR_LIMITS = {"none": 0.3, "drop first 'in a vehicle' flags": 0.3, "drop first vehicle index": 0.5}


def wire_payload(packet):
    length = (packet[3] ** 2 + packet[4] ** 2) ** 0.5
    if length < 0.5:
        return None
    return int(length - 1.0 + 0.5)


def car_entry(mode, receiver_fps=60.0, seed=33):
    """Host gets into a car at CAR_MOUNT; seconds until the joiner shows it, its hides, what was dropped."""
    host, joiner = make_pair(SHARED + HOST_ONLY, SHARED + JOINER_ONLY, seed=seed)
    joiner.lua.execute(live.VEHICLE_MOCK)
    joiner.peer.dt = 1.0 / receiver_fps
    seen = {"flags": 0, "vehicle": 0}
    dropped = []
    send = host.peer.send_burst

    def send_burst(t, pushes, peer):
        payload = wire_payload(pushes[-1])
        if payload is not None and t >= CAR_MOUNT - 0.05:
            if kind(payload) == FLAGS and payload & IN_VEHICLE:
                seen["flags"] += 1
                if mode == "drop first 'in a vehicle' flags" and seen["flags"] == 1:
                    host.peer.local_sequence += len(pushes)
                    dropped.append((round(t, 3), "flags"))
                    return
            if kind(payload) == VEHICLE:
                seen["vehicle"] += 1
                if mode == "drop first vehicle index" and seen["vehicle"] == 1:
                    host.peer.local_sequence += len(pushes)
                    dropped.append((round(t, 3), "vehicle"))
                    return
        send(t, pushes, peer)

    host.peer.send_burst = send_burst
    shown = {}

    def on_frame(peer, t):
        if peer is host.peer:
            if t >= CAR_MOUNT and host.lua.globals().localFlags != IN_VEHICLE:
                host.set_flags(t, IN_VEHICLE)
                shown["hides before"] = int(joiner.lua.globals().carHides)  # sync ON hides once
        elif "t" not in shown and joiner.lua.globals().carPose is not None:
            shown["t"] = t

    saved_loss = timing.LOSS
    timing.LOSS = 0.0  # only the packets this test drops
    try:
        timing.run_pair(host.peer, joiner.peer, CAR_MOUNT + 3.0, on_frame)
    finally:
        timing.LOSS = saved_loss
    delay = shown["t"] - CAR_MOUNT if "t" in shown else None
    return delay, int(joiner.lua.globals().carHides) - shown["hides before"], dropped


def test_car_entry_under_loss():
    ok = True
    cases = [(mode, 60.0, 33) for mode in CAR_LIMITS] + [("none", fps, seed) for fps in (24.0, 27.0) for seed in (33, 34)]
    for mode, fps, seed in cases:
        delay, hides, dropped = car_entry(mode, fps, seed)
        limit = CAR_LIMITS[mode] if fps == 60.0 else 0.5
        passed = delay is not None and delay <= limit and hides == 0
        print(f"  {mode:32s} joiner {fps:.0f} fps seed {seed}: car shown "
              f"{'never' if delay is None else f'+{delay:.2f} s'} after mounting (limit {limit} s), "
              f"car hides after mounting {hides}, dropped {dropped} {'ok' if passed else 'TOO LATE'}")
        ok = ok and passed
    return ok


# ------------------------------------------------------------------ P7

def slow_host(mounted, seconds=120.0, seed=35):
    """Host at 20 fps (20 slots/s), joiner at 60; mounted from 20 s when asked."""
    host, joiner = make_pair(SHARED + HOST_ONLY, SHARED + JOINER_ONLY, seed=seed)
    host.peer.dt = 1.0 / 20.0

    def on_frame(peer, t):
        if mounted and peer is host.peer and t >= 20.0 and host.lua.globals().localFlags != IN_VEHICLE:
            host.set_flags(t, IN_VEHICLE)

    timing.run_pair(host.peer, joiner.peer, seconds, on_frame)
    return host


def test_slow_host():
    ok = True
    for mounted in (False, True):
        host = slow_host(mounted)
        slots = host.slots()
        burst_end = max((x[1] for x in slots if x[3]), default=0.0)
        start = max(burst_end + 1.0, 21.0 if mounted else 0.0)
        after = [x for x in slots if x[1] > start]
        rates = {name: rate([x[1] for x in after if kind(x[2]) == wanted])
                 for name, wanted in (("ping", PING), ("time", TIME), ("weather", WEATHER), ("vehicle", VEHICLE))}
        time_slots = [x[1] for x in after if kind(x[2]) == TIME]
        longest_gap = max((b - a for a, b in zip(time_slots, time_slots[1:])), default=float("inf"))
        # the 3-minute time value steps every 22.5 s: a change still goes out at once
        delays = []
        for k in range(1, int(slots[-1][1] / 22.5)):
            value = (600 + 3 * k) // 3
            first = next((x[1] for x in slots if kind(x[2]) == TIME and x[2] % TYPE_STRIDE == value), None)
            delays.append(None if first is None else first - k * 22.5)
        repeat_min = 0.3 if mounted else 0.5
        checks = [
            share(after) >= FLAG_SHARE_MIN,
            longest_extra_run(after) <= 2,
            rates["ping"] >= 0.75,
            rates["time"] >= repeat_min and rates["weather"] >= repeat_min,
            longest_gap <= 6.0,  # the joiner's TIME_FRESH_SECONDS is 10
            None not in delays and max(delays) <= 0.2,
        ]
        if mounted:
            first_flag = next(i for i, x in enumerate(slots) if kind(x[2]) == FLAGS and x[2] & IN_VEHICLE)
            first_index = next((i for i, x in enumerate(slots) if kind(x[2]) == VEHICLE), None)
            checks += [first_index is not None and first_flag < first_index <= first_flag + 3,
                       rates["vehicle"] >= repeat_min]
        print(f"  host 20 fps {'mounted' if mounted else 'on foot'}: flags {share(after):.2f} % of {len(after)} slots "
              f"after {start:.1f} s, longest non-flag run {longest_extra_run(after)}; "
              + ", ".join(f"{name} {value:.2f} Hz" for name, value in rates.items())
              + f"; longest time gap {longest_gap:.2f} s; time change out after "
              f"{max(d for d in delays if d is not None):.3f} s (missing {delays.count(None)})")
        ok = ok and all(checks)
    return ok


# ------------------------------------------------------------------ P5

def test_partner_reload():
    host, joiner = make_pair(SHARED + HOST_ONLY, SHARED + JOINER_ONLY, seed=34)
    host_n = len(SHARED + HOST_ONLY)
    loads = [(60.0, 66.0), (150.0, 150.0 + 1.5 / 60)]  # 6 s loading screen, then one frame (no pause)

    def on_frame(peer, t):
        if peer is joiner.peer:
            joiner.lua.globals().preGame = any(start <= t < end for start, end in loads)

    timing.run_pair(host.peer, joiner.peer, 240.0, on_frame)
    expected_joiner = (len(SHARED + JOINER_ONLY), host_n, len(SHARED), len(JOINER_ONLY), len(HOST_ONLY))
    events = [(t, line) for t, line in joiner.stamped if "mods compared" in line]
    host_slots = host.slots()
    ok = True
    for start, end in loads:
        again = [(t, line) for t, line in events if end <= t <= end + 30.0]
        resent = sum(1 for s in host_slots if end <= s[1] <= end + 30.0 and kind(s[2]) in (MOD_HI, MOD_LO))
        print(f"  joiner loads {start:.0f}-{end:.2f} s: compares again at "
              f"{[round(t, 1) for t, _ in again]}, host sends {resent} mod payloads in the next 30 s "
              f"(two full lists = {4 * host_n}); {[line.split('EVENT ', 1)[1] for _, line in again]}")
        ok = ok and len(again) == 1 and resent >= 4 * host_n
    # both settle back to one pair every 10 s
    quiet = {side.role: sum(1 for s in side.slots() if s[1] >= 200.0 and kind(s[2]) in (MOD_HI, MOD_LO))
             for side in (host, joiner)}
    host_events = comparisons(host)
    panel = panel_comparison(joiner)
    print(f"  mod payloads in 200-240 s: {quiet} (one pair per 10 s = 8); host compared {len(host_events)}x; "
          f"joiner panel at the end {panel}, want {expected_joiner}")
    return ok and all(count <= 10 for count in quiet.values()) and len(host_events) == 1 and panel == expected_joiner


if __name__ == "__main__":
    results = {}
    try:
        host, joiner = run_main()
        main_runs = True
    except Exception as error:  # report and keep going
        print(f"  EXCEPTION {error!r}")
        main_runs = False
    tests = {
        "P1 10,000 slots per role: flags >= 85 %, time/weather/ping 1 Hz, mods twice in 10 s, exact": (
            lambda: main_runs and test_main_run(host, joiner)),
        "P2 time and weather changes go out at once": lambda: main_runs and test_world_changes_at_once(host),
        "P3 long mod lists: burst ends, flags >= 85 % after it, exact comparison": test_long_lists,
        "P4 vehicle index after the 'in a vehicle' flags, again after 0.2 s, >= 0.5 Hz; flags >= 85 % mounted": test_vehicle_index,
        "P5 partner reload / reset: lists sent again, compared exactly, back to one pair per 10 s": test_partner_reload,
        "P6 car entry: one lost 'in a vehicle' flags or index packet, or a 24/27 fps joiner, shows the car within 0.3-0.5 s": test_car_entry_under_loss,
        "P7 host at 20 fps, on foot and mounted: flags >= 85 %, ping >= 0.75 Hz, time / weather / index repeat, changes at once": test_slow_host,
    }
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
