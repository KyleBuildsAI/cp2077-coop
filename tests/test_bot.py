"""Host runs the test-pattern bot; check the joiner's avatar sees every phase.

Uses the two-instance harness from test_two_players.py with LA<->Warsaw<->Russia latency.
"""
import math
import os
import re
import sys

import test_two_players as harness

FRAME_DT = harness.FRAME_DT


def car_pose_errors(host_truth, car_samples, settle=0.5):
    """Compare the joiner's car with the bot's true pose at the same moment.

    The true pose at time t is interpolated between the host's 30 Hz bot packets.
    Only frames at least `settle` s into steady vehicle speed count: the bot jumps
    from 0 to 14 m/s in one frame, which no real car does.
    """
    def unit(fx, fy):
        length = math.hypot(fx, fy)
        return fx / length, fy / length

    fast_since = None
    fast = []  # (t_start, t_end, sample, next_sample, steady_since)
    for previous, sample in zip(host_truth, host_truth[1:]):
        speed = math.hypot(sample[1] - previous[1], sample[2] - previous[2]) / max(sample[0] - previous[0], 1e-6)
        if speed > 10.0:
            fast_since = previous[0] if fast_since is None else fast_since
            fast.append((previous, sample, fast_since))
        else:
            fast_since = None
    position_errors, yaw_errors = [], []
    index = 0
    for t, x, y, fx, fy in car_samples:
        while index < len(fast) and fast[index][1][0] < t:
            index += 1
        if index >= len(fast):
            break
        before, after, since = fast[index]
        if not (before[0] <= t <= after[0]) or t - since < settle:
            continue
        mix = (t - before[0]) / (after[0] - before[0])
        true_x = before[1] + (after[1] - before[1]) * mix
        true_y = before[2] + (after[2] - before[2]) * mix
        true_fx, true_fy = unit(before[3] + (after[3] - before[3]) * mix, before[4] + (after[4] - before[4]) * mix)
        car_fx, car_fy = unit(fx, fy)
        position_errors.append(math.hypot(x - true_x, y - true_y))
        yaw_errors.append(math.degrees(abs(math.atan2(car_fx * true_fy - car_fy * true_fx, car_fx * true_fx + car_fy * true_fy))))
    if not position_errors:
        return {"frames": 0, "pos_avg": 99.0, "pos_p95": 99.0, "pos_max": 99.0, "yaw_avg": 99.0, "yaw_p95": 99.0}
    position_errors.sort()
    yaw_errors.sort()
    p95 = int(0.95 * (len(position_errors) - 1))
    return {
        "frames": len(position_errors),
        "pos_avg": sum(position_errors) / len(position_errors),
        "pos_p95": position_errors[p95],
        "pos_max": position_errors[-1],
        "yaw_avg": sum(yaw_errors) / len(yaw_errors),
        "yaw_p95": yaw_errors[p95],
    }


def main():
    host = harness.make_instance("host", (100.0, 50.0))
    joiner = harness.make_instance("joiner", (-900.0, 400.0))
    joiner.execute(r"""
        applied = { stance = {}, weapon = {}, vehicleShow = {}, vehicleHide = 0 }
        function player:CP2077Coop_ApplyRemoteStance(c) applied.stance[#applied.stance + 1] = c end
        function player:CP2077Coop_ApplyRemoteWeapon(cls, drawn) applied.weapon[#applied.weapon + 1] = { cls, drawn } end
        carPose = nil
        function player:CP2077Coop_ShowRemoteVehicle(index, x, y, z, fx, fy, slope) applied.vehicleShow[#applied.vehicleShow + 1] = index; carPose = { x, y, fx, fy }; return true end
        function player:CP2077Coop_HideRemoteVehicle() applied.vehicleHide = applied.vehicleHide + 1; carPose = nil end
    """)

    # start bot on host via its panel button (one draw call with the button "clicked")
    instances = {"host": host, "joiner": joiner}
    peers = {"host": "joiner", "joiner": "host"}
    in_flight = []
    seq = {"host": 0, "joiner": 0}
    import random
    rng = random.Random(9)
    t = 0.0
    started = False
    host_truth = []  # (t, x, y, fx, fy) the host's bot pose as it sent it (true pose at t)
    car_samples = []  # (t, x, y, fx, fy) where the joiner's copy of the bot's car stands each frame
    while t < 52.0:
        if not started and t > 3.0:
            host.globals().clickButton = "Start test pattern"
            host.globals().events["onDraw"]()
            host.globals().clickButton = None
            started = True
        for name, lua in instances.items():
            g = lua.globals()
            g.simTime = t
            due = sorted(p for p in in_flight if p[1] == name and p[0] <= t)
            in_flight[:] = [p for p in in_flight if not (p[1] == name and p[0] <= t)]
            for _, _, pseq, packet in due:
                g.net.has = True
                g.net.seq = pseq
                g.net.x, g.net.y, g.net.z, g.net.fx, g.net.fy = packet
            g.tickSpawn()
            g.events["onUpdate"](FRAME_DT)
            lua.eval("stepNpc")(FRAME_DT)
            outbox = g.outbox
            if name == "joiner" and g.carPose is not None:
                car_samples.append((t, g.carPose[1], g.carPose[2], g.carPose[3], g.carPose[4]))
            for index in range(1, len(outbox) + 1):
                p = outbox[index]
                if name == "host" and started:
                    host_truth.append((t, p[1], p[2], p[4], p[5]))
                seq[name] += 1
                delay = (harness.LEG_MS[name] + harness.LEG_MS[peers[name]] + rng.random() * harness.JITTER_MS) / 1000.0
                in_flight.append((t + delay, peers[name], seq[name], (p[1], p[2], p[3], p[4], p[5])))
            lua.execute("outbox = {}")
        t += FRAME_DT

    host_logs = harness.logs(host)
    phases = [re.search(r"phase=(\S+)", l).group(1) for l in host_logs if "BOT phase=" in l]
    print("host bot phases:", phases)

    stats = [harness.parse_stats(l) if hasattr(harness, "parse_stats") else dict(re.findall(r"(\w+)=(\S+)", l)) for l in harness.logs(joiner) if "[STATS]" in l]
    for s in stats:
        print(f"  joiner stats: state={s.get('state')} speed={s.get('remote_speed')} move={s.get('move')} flags={s.get('remote_flags')} drift avg/max={s.get('drift_avg_m')}/{s.get('drift_max_m')}")

    applied = joiner.globals().applied
    stance = list(applied.stance.values())
    weapons = [tuple(w.values()) for w in applied.weapon.values()]
    print("joiner stance calls:", stance)
    print("joiner weapon calls:", weapons)

    vehicle_shows = list(applied.vehicleShow.values())
    print("joiner vehicle shows:", len(vehicle_shows), "indexes:", sorted(set(vehicle_shows)), "hides:", applied.vehicleHide)
    max_speed = max(float(s.get("remote_speed", 0)) for s in stats)
    flags_seen = {int(s.get("remote_flags", 0)) % 256 for s in stats}
    car = car_pose_errors(host_truth, car_samples)
    print("joiner car vs bot true pose (vehicle phase, %d frames): position avg %.2f m p95 %.2f m max %.2f m, heading avg %.1f deg p95 %.1f deg"
          % (car["frames"], car["pos_avg"], car["pos_p95"], car["pos_max"], car["yaw_avg"], car["yaw_p95"]))
    # window 1 holds the bot start jump; the seeded run's worst later window is about 5 m
    drift_maxima = [float(s["drift_max_m"]) for s in stats[1:] if s.get("drift_max_m") not in (None, "-")]
    print("joiner drift max per window after start:", drift_maxima)
    checks = {
        "joiner car matches bot pose (avg < 0.2 m, p95 < 0.4 m, heading avg < 2 deg)": car["frames"] > 200 and car["pos_avg"] < 0.2 and car["pos_p95"] < 0.4 and car["yaw_avg"] < 2.0,
        "all bot phases ran": {"walk", "run", "sprint", "pistol", "pistol-aim", "crouch-walk", "crouch-rifle", "dodge", "vehicle"}.issubset(phases),
        "avatar crouched and stood": True in stance and stance.count(False) >= 1,
        "avatar drew pistol and rifle": (1, True) in weapons and (2, True) in weapons,
        "avatar holstered": any(not drawn for _, drawn in weapons[1:]),
        "joiner saw vehicle speed": max_speed > 9.0,
        "remote vehicle #35 shown then hidden": set(vehicle_shows) == {35} and len(vehicle_shows) > 100 and applied.vehicleHide >= 1,
        "joiner saw crouch flag": any(f % 2 == 1 for f in flags_seen) or True in stance,
        "drift bounded after start (>= 5 windows, every max < 6 m)": len(drift_maxima) >= 5 and max(drift_maxima) < 6.0,
    }
    for name, passed in checks.items():
        print(f"{'PASS' if passed else 'FAIL'}  {name}")
    return all(checks.values())


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
