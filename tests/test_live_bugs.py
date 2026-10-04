"""Regression tests for the bugs seen in the live two-instance test.

LIVE-1  join teleport: Teleport needs EulerAngles (a Quaternion failed every
        attempt), and the joiner should face the host's direction.
LIVE-2  fast follow: during fast movement the avatar must follow by teleport
        only; an AIMoveTo sent between packets overrode the pending teleport
        and the avatar fell 14-16 m behind. The >= 6 m correction is rate limited.
LIVE-3  vehicles: the sender sends the car origin, the receiver extrapolates the
        car to "now" every frame, the avatar waits hidden while the car stands
        in for the remote player, and the car is hidden on reset / LOST.

Usage: python test_live_bugs.py path/to/init.lua
"""
import math
import random
import sys

import test_two_players as harness
from lupa.luajit21 import LuaRuntime

import coop_sim30 as sim

SEND_INTERVAL = 1.0 / 30.0
TYPE_STRIDE = 512
TYPE_FLAGS, TYPE_PING, TYPE_PONG, TYPE_VEHICLE = 0, 3, 4, 5
FLAG_IN_VEHICLE = 16
VEHICLE_INDEX = 35

# An AITeleportCommand runs on a later AI tick. A newer teleport only updates
# the pending one; any other AI command sent before it runs replaces it (that
# is how the avatar got stuck 14-16 m behind the car in the live test).
AI_TELEPORT_MOCK = r"""
TELE_LAT = 0.05
teleportsIgnored = false
npcFrozen = false
local stepWalk = stepNpc
function stepNpc(dt)
    if not npcFrozen then stepWalk(dt) end
end
pendingTeleport = nil
supersededTeleports = 0
wrappedController = false
visibleCalls = {}
function player:CP2077Coop_MoveRemoteTest(x, y, z)
    stats.teleports = stats.teleports + 1
    if npc == nil or teleportsIgnored then return end
    if pendingTeleport ~= nil then
        pendingTeleport.x, pendingTeleport.y, pendingTeleport.z = x, y, z
    else
        pendingTeleport = { at = simTime + TELE_LAT, x = x, y = y, z = z }
    end
end
function stepAi()
    if npc == nil then return end
    if not wrappedController then
        wrappedController = true
        local controller = npc:GetAIControllerComponent()
        local send = controller.SendCommand
        function controller:SendCommand(command)
            if pendingTeleport ~= nil then
                pendingTeleport = nil
                supersededTeleports = supersededTeleports + 1
            end
            return send(self, command)
        end
    end
    if pendingTeleport ~= nil and simTime >= pendingTeleport.at then
        npc.x, npc.y, npc.z = pendingTeleport.x, pendingTeleport.y, pendingTeleport.z
        npc.target = nil
        npc.pending = nil
        pendingTeleport = nil
    end
end
"""

VEHICLE_MOCK = r"""
carPose = nil
carHides = 0
carShows = 0
function player:CP2077Coop_ShowRemoteVehicle(index, x, y, z, fx, fy)
    carShows = carShows + 1
    carPose = { x = x, y = y, z = z, fx = fx, fy = fy, index = index }
    return true
end
function player:CP2077Coop_HideRemoteVehicle() carHides = carHides + 1; carPose = nil end
function player:CP2077Coop_SetRemoteAvatarVisible(visible)
    visibleCalls[#visibleCalls + 1] = { visible = visible, t = simTime }
    return true
end
"""


def make_receiver(role="host", position=(0.0, 0.0), extra=""):
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().simTime = 0.0
    lua.execute(sim.MOCK)
    lua.execute(harness.IMGUI_MOCK)
    lua.execute(harness.PLAYER_EXTRA.replace("PX", str(position[0])).replace("PY", str(position[1])))
    lua.execute(AI_TELEPORT_MOCK)
    lua.execute(extra)
    source = open(harness.SCRIPT, encoding="utf-8").read()
    if role == "joiner":
        source = source.replace("local IS_HOST = true", "local IS_HOST = false", 1)
    lua.execute(source)
    lua.globals().events["onInit"]()
    return lua


def decode_payload(fx, fy):
    length = math.hypot(fx, fy)
    if length < 0.5:
        return None
    return int(math.floor(length - 1.0 + 0.5))


class ScriptedRemote:
    """The other player: follows path(t) -> (x, y, z, fx, fy, flags) and talks
    the mod's wire format (flags, vehicle index, pong) over a lossy link."""

    def __init__(self, receiver, path, latency=0.115, jitter=0.020, loss=0.01,
                 sender_fps=60.0, seed=5):
        self.lua = receiver
        self.g = receiver.globals()
        self.path = path
        self.latency = latency
        self.jitter = jitter
        self.loss = loss
        self.sender_dt = 1.0 / sender_fps
        self.rng = random.Random(seed)
        self.in_flight = []
        self.sequence = 0
        self.slot = 0
        self.pong = None
        self.pings = []  # (arrive_time, token): the receiver's pings on their way here
        self.next_frame = 0.0
        self.accumulator = 0.0
        self.silent_from = None

    def _payload(self, flags):
        if self.pong is not None:
            token, self.pong = self.pong, None
            return TYPE_PONG * TYPE_STRIDE + token
        self.slot += 1
        if flags & FLAG_IN_VEHICLE and self.slot % 6 == 3:
            return TYPE_VEHICLE * TYPE_STRIDE + VEHICLE_INDEX
        return TYPE_FLAGS * TYPE_STRIDE + flags

    def _send(self, t):
        x, y, z, fx, fy, flags = self.path(t)
        scale = 1.0 + self._payload(flags)
        self.sequence += 1
        if self.rng.random() < self.loss:
            return
        self.in_flight.append((t + self._delay(), self.sequence, (x, y, z, fx * scale, fy * scale)))

    def _delay(self):
        return self.latency + (self.rng.random() * 2.0 - 1.0) * self.jitter

    def tick(self, t):
        # sender frames at its own rate. Like the mod's onUpdate: send first
        # (same accumulator), then read what arrived, so a ping is answered
        # in the next send slot after the frame that saw it.
        while self.next_frame <= t:
            frame = self.next_frame
            if self.silent_from is None or frame < self.silent_from:
                self.accumulator += self.sender_dt
                if self.accumulator >= SEND_INTERVAL:
                    self.accumulator = min(self.accumulator - SEND_INTERVAL, 2 * SEND_INTERVAL)
                    self._send(frame)
            for arrive, token in [p for p in self.pings if p[0] <= frame]:
                self.pong = token
            self.pings = [p for p in self.pings if p[0] > frame]
            self.next_frame += self.sender_dt
        # DLL keeps the packet that arrived last
        arrived = sorted((p for p in self.in_flight if p[0] <= t), key=lambda p: p[0])
        self.in_flight = [p for p in self.in_flight if p[0] > t]
        net = self.g.net
        for _, sequence, (x, y, z, fx, fy) in arrived:
            net.has = True
            net.seq, net.x, net.y, net.z, net.fx, net.fy = sequence, x, y, z, fx, fy

    def read_outbox(self, t):
        # the receiver's pings travel here over the same link and get a pong
        outbox = self.g.outbox
        for index in range(1, len(outbox) + 1):
            packet = outbox[index]
            payload = decode_payload(packet[4], packet[5])
            if payload is not None and payload // TYPE_STRIDE == TYPE_PING and self.rng.random() >= self.loss:
                self.pings.append((t + self._delay(), payload % TYPE_STRIDE))
        self.lua.execute("outbox = {}")


def run(receiver, remote, seconds, fps, on_frame=None, start=0.0):
    g = receiver.globals()
    t = start
    dt = 1.0 / fps
    while t < seconds:
        g.simTime = t
        remote.tick(t)
        g.tickSpawn()
        g.events["onUpdate"](dt)
        receiver.eval("stepNpc")(dt)
        receiver.eval("stepAi")()
        remote.read_outbox(t)
        if on_frame is not None:
            on_frame(t)
        t += dt
    return t


def logs(lua):
    return list(lua.globals().logs.values())


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[int(fraction * (len(ordered) - 1))] if ordered else float("nan")


# ------------------------------------------------------------------ LIVE-1

def test_join_teleport_uses_euler_angles():
    host = harness.make_instance("host", (100.0, 50.0))
    # host looks along +X: the joiner must face the same way (yaw -90 deg)
    host.execute("function player:GetWorldForward() return { x = 1, y = 0, z = 0, w = 0 } end")
    joiner = harness.make_instance("joiner", (-900.0, 400.0))
    instances = {"host": host, "joiner": joiner}
    peers = {"host": "joiner", "joiner": "host"}
    in_flight, sequence, t = [], {"host": 0, "joiner": 0}, 0.0
    # the join waits until the joiner has been in the game for 4 s
    while t < 6.0:
        for name, lua in instances.items():
            g = lua.globals()
            g.simTime = t
            due = sorted(p for p in in_flight if p[1] == name and p[0] <= t)
            in_flight[:] = [p for p in in_flight if not (p[1] == name and p[0] <= t)]
            for _, _, packet_sequence, packet in due:
                g.net.has = True
                g.net.seq = packet_sequence
                g.net.x, g.net.y, g.net.z, g.net.fx, g.net.fy = packet
            g.tickSpawn()
            g.events["onUpdate"](harness.FRAME_DT)
            lua.eval("stepNpc")(harness.FRAME_DT)
            outbox = g.outbox
            for index in range(1, len(outbox) + 1):
                p = outbox[index]
                sequence[name] += 1
                in_flight.append((t + 0.12, peers[name], sequence[name], (p[1], p[2], p[3], p[4], p[5])))
            lua.execute("outbox = {}")
        t += harness.FRAME_DT
    g = joiner.globals()
    errors = [l for l in logs(joiner) if "TELEPORT ERROR" in l]
    synced = [l for l in logs(joiner) if "WORLD SYNC OK" in l]
    yaw = g.playerYaw
    print(f"  join: sync={synced[:1]} errors={errors[:1]} joiner yaw={yaw}")
    join_ok = bool(synced) and not errors and yaw is not None and abs(yaw - (-90.0)) < 0.5

    # panel "Go to test area" goes through the same helper (keeps own facing)
    g.clickButton = "Go to test area"
    g.events["onDraw"]()
    g.clickButton = None
    g.events["onUpdate"](harness.FRAME_DT)
    area_logs = [l for l in logs(joiner) if "teleported to test area" in l]
    at_area = abs(g.playerPos.x - (-1818.82)) < 0.01 and abs(g.playerPos.y - 3858.03) < 0.01
    print(f"  test area: {area_logs[:1]} at area={at_area} yaw={g.playerYaw}")
    area_ok = bool(area_logs) and at_area and abs(g.playerYaw) < 0.5 and not [l for l in logs(joiner) if "TELEPORT ERROR" in l]
    return join_ok and area_ok


# ------------------------------------------------------------------ LIVE-2

def straight_path(speed, flags=0, start=(20.0, 0.0), fast_from=1.0):
    def path(t):
        moving = max(0.0, t - fast_from)
        return start[0], start[1] + speed * moving, 0.0, 0.0, 1.0, flags
    return path


def fast_follow(fps, flags):
    receiver = make_receiver()
    path = straight_path(14.0, flags)
    remote = ScriptedRemote(receiver, path, latency=0.115, jitter=0.020, loss=0.01)
    g = receiver.globals()
    samples = []
    marks = {}

    def on_frame(t):
        if g.npc is None:
            return
        if t >= 3.0 and "start" not in marks:
            marks["start"] = (t, g.stats.teleports, g.stats.moveCommands, g.net.seq)
        if t >= 3.0:
            x, y = path(t)[:2]
            samples.append(math.hypot(g.npc.x - x, g.npc.y - y))

    end = run(receiver, remote, 9.0, fps, on_frame)
    start, teleports0, moves0, sequence0 = marks["start"]
    duration = end - start
    teleports = (g.stats.teleports - teleports0) / duration
    moves = (g.stats.moveCommands - moves0) / duration
    packets = (g.net.seq - sequence0) / duration
    average = sum(samples) / len(samples)
    print(f"  {fps:3d} fps flags={flags:2d}: avatar behind remote avg {average:.2f} m max {max(samples):.2f} m, "
          f"teleports {teleports:.1f}/s (packets {packets:.1f}/s), moveTo {moves:.1f}/s, superseded {g.supersededTeleports}")
    return average < 3.5 and max(samples) < 6.0 and teleports <= packets + 0.5 and moves < 1.0


def test_fast_follow_teleport_only():
    results = [fast_follow(fps, flags) for fps in (60, 144) for flags in (0, FLAG_IN_VEHICLE)]
    return all(results)


def test_unresponsive_avatar_backs_off():
    receiver = make_receiver()
    remote = ScriptedRemote(receiver, straight_path(4.5, start=(40.0, 0.0), fast_from=0.0), loss=0.0)
    g = receiver.globals()
    marks = {}

    def on_frame(t):
        if t >= 3.0 and not g.teleportsIgnored:
            g.teleportsIgnored = True  # avatar dead / ragdolled: ignores every command
            g.npcFrozen = True
        if t >= 5.0 and "start" not in marks:
            marks["start"] = g.stats.teleports

    run(receiver, remote, 15.0, 144, on_frame)
    calls = g.stats.teleports - marks["start"]
    warned = [l for l in logs(receiver) if "NOT RESPONDING" in l]
    print(f"  stuck avatar at 144 fps: {calls} teleports in 10 s, warning logged {len(warned)}x")
    return calls <= 15 and len(warned) == 1


# the avatar dies (stray fire, a fall): DespawnRemote removes it, the next spawn is a new, live NPC
DEAD_MOCK = r"""
despawns = 0
function player:CP2077Coop_DespawnRemote()
    despawns = despawns + 1
    npc = nil
    spawnAt = nil
    pendingTeleport = nil
    teleportsIgnored = false
    npcFrozen = false
    wrappedController = false
end
"""


def test_dead_avatar_respawned():
    receiver = make_receiver(extra=DEAD_MOCK)
    remote = ScriptedRemote(receiver, straight_path(4.5, start=(40.0, 0.0), fast_from=0.0), loss=0.0)
    g = receiver.globals()
    marks = {}

    def on_frame(t):
        if t >= 3.0 and "dead" not in marks:
            marks["dead"] = g.npc
            g.npc.dead = True
            g.teleportsIgnored = True
            g.npcFrozen = True

    run(receiver, remote, 15.0, 60, on_frame)
    lines = logs(receiver)
    dropped = [l for l in lines if "avatar despawned" in l]
    spawns = [l for l in lines if "remote spawn requested" in l]
    npc = g.npc
    x, y = straight_path(4.5, start=(40.0, 0.0), fast_from=0.0)(15.0)[:2]
    error = math.hypot(npc.x - x, npc.y - y) if npc is not None else None
    print(f"  avatar dies at 3 s: despawns {int(g.despawns)}, {dropped}, spawn requests {len(spawns)}, "
          f"new avatar {error if error is None else round(error, 2)} m from the partner at 15 s")
    return (int(g.despawns) == 1 and len(dropped) == 1 and "dead" in dropped[0] and len(spawns) == 2
            and npc is not None and npc is not marks["dead"] and error < 3.0)


# ------------------------------------------------------------------ LIVE-3

def test_sender_sends_vehicle_origin():
    lua = make_receiver(position=(10.0, 20.0), extra=r"""
        localFlags = 16
        function player:CP2077Coop_GetMountedVehiclePose() return { 12.0, 19.0, 0.4, 0.6, 0.8 } end
    """)
    g = lua.globals()
    sent = []
    t = 0.0
    while t < 1.0:
        g.simTime = t
        g.events["onUpdate"](1 / 60)
        outbox = g.outbox
        for index in range(1, len(outbox) + 1):
            sent.append(tuple(outbox[index].values()))
        lua.execute("outbox = {}")
        t += 1 / 60
    later = sent[3:]
    on_car = all(abs(p[0] - 12.0) < 1e-6 and abs(p[1] - 19.0) < 1e-6 and abs(p[2] - 0.4) < 1e-6 for p in later)
    heading = all(abs(math.atan2(p[3], p[4]) - math.atan2(0.6, 0.8)) < 1e-3 for p in later)
    # on foot again: the getter returns nothing, the player's own position goes out
    lua.execute("function player:CP2077Coop_GetMountedVehiclePose() return {} end")
    lua.execute("outbox = {}")
    for _ in range(10):
        g.simTime = t  # keeps the clock moving; the 30 Hz schedule itself runs on delta (S.sendAccumulator)
        g.events["onUpdate"](1 / 60)
        t += 1 / 60
    outbox = g.outbox
    foot_packets = len(outbox)
    # 10 frames x 1/60 s at 30 Hz = 5 packets; a sender that stops after dismounting must not pass
    on_foot = foot_packets >= 4 and all(abs(outbox[i][1] - 10.0) < 1e-6 and abs(outbox[i][2] - 20.0) < 1e-6 for i in range(1, foot_packets + 1))
    print(f"  sender: {len(later)} packets carry the car origin={on_car}, car heading={heading}, "
          f"on foot: {foot_packets} packets -> player position={on_foot}")
    return len(later) > 20 and on_car and heading and on_foot


def drive_path(t):
    """Walk, get in, drive (accelerate, curve, brake), get out, walk."""
    if t < 2.0:
        return 0.0, 1.4 * t, 0.0, 0.0, 1.0, 0
    x, y, heading, speed = 0.0, 2.8, 0.0, 0.0
    step = 1.0 / 240.0
    clock = 2.0
    while clock < min(t, 11.0):
        if clock < 5.0:
            speed = min(22.0, speed + 8.0 * step)      # accelerate
            turn = 0.0
        elif clock < 8.0:
            turn = 0.45                                 # long curve
        else:
            speed = max(0.0, speed - 9.0 * step)        # brake
            turn = -0.2
        heading += turn * step
        x += -math.sin(heading) * speed * step
        y += math.cos(heading) * speed * step
        clock += step
    in_car = t < 11.0
    if not in_car:
        y += 1.4 * (t - 11.0)
    return x, y, 0.0, -math.sin(heading), math.cos(heading), FLAG_IN_VEHICLE if in_car else 0


def car_tracking(fps, sender_fps, latency, jitter, loss):
    receiver = make_receiver(extra=VEHICLE_MOCK)
    remote = ScriptedRemote(receiver, drive_path, latency=latency, jitter=jitter, loss=loss, sender_fps=sender_fps)
    g = receiver.globals()
    position_errors, heading_errors, naive_errors = [], [], []
    last_packet = {}

    def on_frame(t):
        if g.net.has:
            last_packet["pose"] = (g.net.x, g.net.y)
        car = g.carPose
        if car is None or not (3.0 <= t <= 10.5):
            return
        x, y, _, fx, fy, _ = drive_path(t)
        position_errors.append(math.hypot(car.x - x, car.y - y))
        heading_errors.append(abs(math.degrees(math.atan2(car.fx * fy - car.fy * fx, car.fx * fx + car.fy * fy))))
        naive_errors.append(math.hypot(last_packet["pose"][0] - x, last_packet["pose"][1] - y))

    run(receiver, remote, 13.0, fps, on_frame)
    average = sum(position_errors) / len(position_errors)
    p95 = percentile(position_errors, 0.95)
    heading = sum(heading_errors) / len(heading_errors)
    naive = sum(naive_errors) / len(naive_errors)
    print(f"  car {fps:3d} fps (sender {sender_fps:.0f} fps, {latency * 1000:.0f}+-{jitter * 1000:.0f} ms, loss {loss:.0%}): "
          f"pos avg {average:.2f} m p95 {p95:.2f} m, heading avg {heading:.1f} deg "
          f"(teleport-to-last-packet would be {naive:.2f} m)")
    return average < 0.6 and p95 < 1.2 and heading < 2.0 and average < naive * 0.35


def test_car_extrapolated_every_frame():
    cases = [(60, 60, 0.115, 0.020, 0.01), (144, 60, 0.115, 0.020, 0.01), (40, 45, 0.115, 0.020, 0.01), (60, 60, 0.180, 0.040, 0.03)]
    return all([car_tracking(*case) for case in cases])


def test_avatar_parked_while_driving():
    receiver = make_receiver(extra=VEHICLE_MOCK)
    remote = ScriptedRemote(receiver, drive_path, loss=0.0)
    g = receiver.globals()
    marks = {}
    after_exit = []

    def on_frame(t):
        if t >= 3.5 and "drive" not in marks:
            marks["drive"] = (g.stats.teleports, g.stats.moveCommands, g.stats.rotates)
        if t >= 10.9 and "exit" not in marks:
            marks["exit"] = (g.stats.teleports, g.stats.moveCommands, g.stats.rotates)
        if t >= 12.5 and g.npc is not None:
            x, y = drive_path(t)[:2]
            after_exit.append(math.hypot(g.npc.x - x, g.npc.y - y))

    run(receiver, remote, 14.0, 60, on_frame)
    commands_while_driving = [b - a for a, b in zip(marks["drive"], marks["exit"])]
    calls = [(c.visible, round(c.t, 2)) for c in g.visibleCalls.values()]
    parked = [l for l in logs(receiver) if "avatar parked" in l]
    print(f"  park: AI commands while driving (teleport, moveTo, rotate) = {commands_while_driving}, "
          f"visibility calls {calls}, car hidden {g.carHides}x, avatar after exit {max(after_exit):.2f} m from remote")
    return (
        commands_while_driving == [0, 0, 0]
        and len(parked) == 1
        and [visible for visible, _ in calls] == [False, True]
        and calls[1][1] > 11.0
        and g.carHides >= 1
        and g.carPose is None
        and max(after_exit) < 1.5
    )


def test_car_hidden_on_lost_and_reset():
    # LOST: the remote drives, then goes silent
    receiver = make_receiver(extra=VEHICLE_MOCK)
    remote = ScriptedRemote(receiver, drive_path, loss=0.0)
    g = receiver.globals()
    remote.silent_from = 6.0
    run(receiver, remote, 12.5, 60)
    lost_hidden = g.carPose is None and g.carHides >= 1 and any("connection lost" in l for l in logs(receiver))
    shown_again = [c.visible for c in g.visibleCalls.values()] == [False, True]

    # reset (role switch) while the car is shown
    receiver2 = make_receiver(extra=VEHICLE_MOCK)
    remote2 = ScriptedRemote(receiver2, drive_path, loss=0.0)
    g2 = receiver2.globals()
    end = run(receiver2, remote2, 6.0, 60)
    shown_before = g2.carPose is not None
    g2.clickButton = "Switch to JOINER"
    g2.events["onDraw"]()
    g2.clickButton = None
    g2.simTime = end
    g2.events["onUpdate"](1 / 60)
    reset_hidden = g2.carPose is None and g2.carHides >= 1
    reset_shown = [c.visible for c in g2.visibleCalls.values()][-1:] == [True]
    print(f"  LOST: car hidden={lost_hidden} avatar shown again={shown_again}; "
          f"reset: car was shown={shown_before} hidden={reset_hidden} avatar shown={reset_shown}")
    return lost_hidden and shown_again and shown_before and reset_hidden and reset_shown


FIND_DIAG = r"""
function findDiag(onUpdate)
    local index = 1
    while true do
        local name, value = debug.getupvalue(onUpdate, index)
        if name == nil then return nil end
        if name == "Diag" then return value end
        index = index + 1
    end
end
"""
MENU_MOCK = r"""
paused = false
Game.GetSystemRequestsHandler = function()
    return { IsPreGame = function() return false end, IsGamePaused = function() return paused end }
end
"""


def jog_path(t):
    """The other player jogs north at 5 m/s from (20, 0) (host bit set)."""
    return 20.0, 5.0 * t, 0.0, 0.0, 1.0, 256


def test_menu_holds_corrections():
    # the receiver spends a minute in the ESC menu while the partner jogs on: the frozen
    # world takes no teleports, so none are sent, counted or reported as not responding
    receiver = make_receiver(role="joiner", position=(18.0, 0.0), extra=MENU_MOCK + FIND_DIAG)
    remote = ScriptedRemote(receiver, jog_path, loss=0.0)
    g = receiver.globals()
    diag = receiver.eval("findDiag")(g.events["onUpdate"])
    t = run(receiver, remote, 8.0, 60)
    hard_before = int(diag.hardTotal)
    g.paused = True
    dt = 1.0 / 60
    while t < 68.0:  # frozen world: the NPC and the AI do not move
        g.simTime = t
        remote.tick(t)
        g.events["onUpdate"](dt)
        remote.read_outbox(t)
        t += dt
    hard_frozen = int(diag.hardTotal) - hard_before
    g.paused = False
    run(receiver, remote, 72.0, 60, start=t)
    npc = g.npc
    x, y = jog_path(72.0)[:2]
    after_error = math.hypot(npc.x - x, npc.y - y) if npc is not None else None
    bad = [l for l in logs(receiver) if "NOT RESPONDING" in l or "SNAP FAILED" in l or "not found" in l]
    frozen_logs = [l for l in logs(receiver) if "local world" in l]
    print(f"  60 s in the menu while the partner jogs: hard corrections in it {hard_frozen}, after it "
          f"{int(diag.hardTotal) - hard_before - hard_frozen}; avatar {after_error if after_error is None else round(after_error, 2)} m "
          f"from the partner 4 s after; bad logs {bad[:3]}; {frozen_logs}")
    return (hard_frozen == 0 and not bad and after_error is not None and after_error < 3.0
            and len(frozen_logs) == 2)


# A new NPC's AI is not running yet: for WARMUP s it reads (0, 0, 0) and every
# AITeleportCommand is dropped (live test 2026-10-04: REMOTE SNAP FAILED at spawn)
WARMUP_MOCK = r"""
WARMUP = 1.0
npcBornAt = nil
teleportsInWarmup = 0
local tickBase = tickSpawn
function tickSpawn()
    local before = npc
    tickBase()
    if before == nil and npc ~= nil then
        npcBornAt = simTime
        local position = npc.GetWorldPosition
        function npc:GetWorldPosition()
            if simTime - npcBornAt < WARMUP then return { x = 0, y = 0, z = 0, w = 1 } end
            return position(self)
        end
    end
    teleportsIgnored = npcBornAt ~= nil and simTime - npcBornAt < WARMUP
end
local move = player.CP2077Coop_MoveRemoteTest
function player:CP2077Coop_MoveRemoteTest(...)
    if teleportsIgnored then teleportsInWarmup = teleportsInWarmup + 1 end
    return move(self, ...)
end
"""


def run_partner(t):
    """The joiner runs north at 4.5 m/s from (20, 0)."""
    return 20.0, 4.5 * t, 0.0, 0.0, 1.0, 0


def test_spawn_at_partner():
    receiver = make_receiver(role="host", position=(0.0, 0.0), extra=WARMUP_MOCK + FIND_DIAG)
    remote = ScriptedRemote(receiver, run_partner, loss=0.0)
    g = receiver.globals()
    diag = receiver.eval("findDiag")(g.events["onUpdate"])
    spawned = {}

    def watch(t):
        if "t" not in spawned and g.spawnAt is not None:
            spawned["t"] = t
            spawned["at"] = (g.spawnAt.x, g.spawnAt.y)

    run(receiver, remote, 12.0, 60, on_frame=watch)
    lines = logs(receiver)
    partner_then = run_partner(spawned.get("t", 0.0))[:2]
    spawn_at = spawned.get("at")
    from_partner = math.hypot(spawn_at[0] - partner_then[0], spawn_at[1] - partner_then[1]) if spawn_at else None
    from_player = math.hypot(spawn_at[0], spawn_at[1]) if spawn_at else None
    snaps = [l for l in lines if "SNAP" in l]
    npc = g.npc
    x, y = run_partner(12.0)[:2]
    end_error = math.hypot(npc.x - x, npc.y - y) if npc is not None else None
    print(f"  spawn at {spawn_at} ({from_partner if from_partner is None else round(from_partner, 2)} m from the partner, "
          f"{from_player if from_player is None else round(from_player, 1)} m from us); teleports while the NPC was not placed "
          f"{int(g.teleportsInWarmup)}; {snaps}; hard corrections {int(diag.hardTotal)}; avatar {end_error if end_error is None else round(end_error, 2)} m "
          f"from the partner at 12 s")
    return (spawn_at is not None and from_partner < 3.0 and from_player > 10.0
            and int(g.teleportsInWarmup) == 0
            and any("SNAP OK" in l for l in snaps) and not any("SNAP FAILED" in l for l in snaps)
            and int(diag.hardTotal) == 0 and end_error is not None and end_error < 3.0)


def test_car_kept_through_long_local_frame():
    # one 5.5 s local frame (window drag, autosave) while the remote drives:
    # packets kept arriving, so the car stays and the avatar stays parked
    receiver = make_receiver(extra=VEHICLE_MOCK)
    remote = ScriptedRemote(receiver, drive_path, loss=0.0)
    g = receiver.globals()
    t = run(receiver, remote, 3.0, 60)
    hides_before = g.carHides
    long_frame = 5.5
    t += long_frame
    g.simTime = t
    remote.tick(t)
    g.tickSpawn()
    g.events["onUpdate"](long_frame)
    receiver.eval("stepNpc")(long_frame)
    receiver.eval("stepAi")()
    remote.read_outbox(t)
    run(receiver, remote, 10.5, 60, start=t + 1 / 60)
    calls = [c.visible for c in g.visibleCalls.values()]
    lost_logs = [l for l in logs(receiver) if "connection lost" in l or "-> LOST" in l]
    print(f"  5.5 s local frame while driving: car hides {g.carHides - hides_before}, "
          f"car shown at 10.5 s={g.carPose is not None}, avatar visibility calls {calls}, LOST logs {lost_logs}")
    return g.carHides == hides_before and g.carPose is not None and calls == [False] and not lost_logs


if __name__ == "__main__":
    tests = {
        "LIVE-1 join teleport passes EulerAngles, joiner faces host, test area uses it too": test_join_teleport_uses_euler_angles,
        "LIVE-2 fast follow: teleport per packet, no AIMoveTo, avatar keeps up (60/144 fps)": test_fast_follow_teleport_only,
        "LIVE-2 unresponsive avatar: teleport retries back off": test_unresponsive_avatar_backs_off,
        "LIVE-2 dead avatar: despawned once at NOT RESPONDING, a new one follows the partner": test_dead_avatar_respawned,
        "LIVE-3 sender sends the car origin and heading while mounted": test_sender_sends_vehicle_origin,
        "LIVE-3 receiver extrapolates the car to now every frame": test_car_extrapolated_every_frame,
        "LIVE-3 avatar parked hidden while the remote drives, snaps back on exit": test_avatar_parked_while_driving,
        "LIVE-3 car hidden on connection LOST and on reset": test_car_hidden_on_lost_and_reset,
        "LIVE-3 one 5.5 s local frame while the remote drives keeps the car (no false LOST)": test_car_kept_through_long_local_frame,
        "LIVE-4 a minute in the ESC menu: no teleports, hard corrections or NOT RESPONDING into the frozen world": test_menu_holds_corrections,
        "LIVE-5 avatar spawned at the partner: no teleport before the game places it, no SNAP FAILED, no hard correction": test_spawn_at_partner,
    }
    results = {}
    for name, test in tests.items():
        print(f"-- {name}")
        try:
            results[name] = test()
        except Exception as error:  # report and keep going
            print(f"  EXCEPTION {error!r}")
            results[name] = False
    for name, passed in results.items():
        print(f"{'PASS' if passed else 'FAIL'}  {name}")
    sys.exit(0 if all(results.values()) else 1)
