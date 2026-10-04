"""Real init.lua + native transport adapter, mocked game/native boundary only."""
import os
import sys

import test_live_bugs as live
import test_timing as timing
import test_two_players as harness

MODULE = os.path.join(os.path.dirname(harness.SCRIPT), "net_transport.lua")
MODULE_SOURCE = open(MODULE, encoding="utf-8").read()
MOCK = r"""
wire = {}
nativePushes, legacyPushes, nativePolls, nativeStops, nativeConnections = 0, 0, 0, 0, 0
sentMessages = {}
sampleText = "interpolated 8 2 3 270 0 3 0 0 2 0 255 120 0"
Game.Net_ConnectV2 = function() nativeConnections = nativeConnections + 1; return true end
Game.Net_Disconnect = function() nativeStops = nativeStops + 1 end
Game.Net_Poll = function() nativePolls = nativePolls + 1; return table.remove(wire, 1) or "" end
Game.Net_SendTo = function(peer, channel, payload)
    sentMessages[#sentMessages + 1] = { peer=peer, channel=channel, payload=payload }
    return true
end
Game.Net_PushPlayer = function(...) nativePushes = nativePushes + 1; return true end
Game.Net_SampleRemote = function() return sampleText end
local legacyPush = Game.CP2077Coop_PushPlayerState
Game.CP2077Coop_PushPlayerState = function(...)
    legacyPushes = legacyPushes + 1
    return legacyPush(...)
end
function player:CP2077Coop_SuppressLegacyCombat(value) combatSuppressed = value end
markerPose = nil
function player:CP2077Coop_ShowRemoteMarker(x,y,z) markerPose={x=x,y=y,z=z}; return true end
function player:CP2077Coop_HideRemoteMarker() markerPose=nil end
preGame = false
Game.GetSystemRequestsHandler = function() return { IsPreGame=function() return preGame end } end
"""


def frame(lua, count=1, delta=1 / 60):
    g = lua.globals()
    for _ in range(count):
        g.simTime += delta
        g.tickSpawn()
        g.events["onUpdate"](delta)
        g.stepNpc(delta)
        g.stepAi()


def enqueue(lua, message):
    q = lua.globals().wire
    q[len(q) + 1] = message


def upvalue(lua, name):
    lua.execute(timing.FIND_UPVALUE)
    return lua.eval("findUpvalue")(lua.globals().events["onUpdate"], name)


def receiver(mode="v2", extra="", role="host"):
    with open("transport.ini", "w", encoding="utf-8") as f:
        f.write(f"mode={mode}\nprobe_disabled=true\nroom=offline-test\n")
    try:
        preload = "package.preload['net_transport'] = function()\n" + MODULE_SOURCE + "\nend\n"
        lua = live.make_receiver(role=role, extra=preload + MOCK + live.VEHICLE_MOCK + extra)
    finally:
        os.remove("transport.ini")
    frame(lua, 3)
    return lua


def welcome(lua, role="joiner"):
    enqueue(lua, "0|0|welcome 1 host")
    enqueue(lua, f"0|0|peer_join 2 {role}")
    enqueue(lua, f"2|30|C3H1|100|{role}|0.0.34")
    frame(lua, 3)


def movement(lua, sequence=1, flags=0, vehicle=-1, source="player"):
    enqueue(lua, f"2|1|C3M1|100|{sequence}|{source}|10|2|3|1|0|3|0|0|{flags}|2|255|0|{vehicle}|0")
    frame(lua)


def test_native_sample_is_not_a_packet_or_second_prediction():
    lua = receiver()
    assert lua.globals().legacyPushes == 0 and lua.globals().combatSuppressed
    welcome(lua)
    movement(lua)
    sync, state, diag, steer = [upvalue(lua, x) for x in ("Sync", "S", "Diag", "Steer")]
    sync.rttMs = 400.0
    assert (state.targetX, state.targetY, state.targetZ) == (8.0, 2.0, 3.0)
    assert state.previousRemoteX == 10.0 and lua.globals().markerPose.x == 10.0
    assert state.remoteVelocityX == 3.0 and steer.endpoint() == (8.0, 2.0, 3.0)
    assert diag.packetsReceived == 1
    frame(lua, 30)
    assert diag.packetsReceived == 1 and state.targetX == 8.0
    pos = lua.table_from({"x": 8, "y": 2, "z": 3})
    diag.recordDrift(pos)
    assert diag.avatarError == 0.0
    frame(lua, 70)  # raw sample silence expires, held native pose cannot refresh it
    assert sync.v2Sample is None and lua.globals().markerPose is None
    assert lua.globals().legacyPushes == 0


def test_native_buffered_target_does_not_reverse_steering_direction():
    lua = receiver()
    welcome(lua)
    movement(lua)
    state, steer = [upvalue(lua, x) for x in ("S", "Steer")]
    state.previousRemoteX, state.previousRemoteY = 10.0, 0.0
    state.targetX, state.targetY, state.targetZ = 8.0, 0.0, 0.0
    state.remoteVelocityX, state.remoteVelocityY = 7.0, 0.0
    steer.remember(8.0, 0.0, 0.0, "Sprint", False)
    assert (steer.dirX, steer.dirY) == (1.0, 0.0), "buffered target is behind raw pose, but motion remains forward"
    current = lua.table_from({"x": 4.0, "y": 0.0, "z": 0.0})
    # A forward-moving endpoint alone must not cancel an active command.
    for index in range(1, 9):
        target = 8.0 + index * 1.75
        state.previousRemoteX, state.targetX = target + 2.0, target
        assert not steer.shouldReissue(current, target, 0.0, 0.0, "Sprint", False, 0.26)
        assert steer.endpoint() == (target, 0.0, 0.0)  # no second prediction
    # Real turn and reverse direction still re-steer promptly.
    state.remoteVelocityX, state.remoteVelocityY = 0.0, 7.0
    assert steer.shouldReissue(current, state.targetX, 0.1, 0.0, "Sprint", False, 0.26)
    state.remoteVelocityX, state.remoteVelocityY = -7.0, 0.0
    assert steer.shouldReissue(current, state.targetX, 0.0, 0.0, "Sprint", False, 0.26)
    # Held/near-zero velocity keeps the previous command direction, rather than
    # normalizing packet noise or deriving the backwards raw-to-sample vector.
    state.remoteVelocityX, state.remoteVelocityY = 0.0, 0.0
    assert not steer.shouldReissue(current, state.targetX, 0.0, 0.0, "Sprint", False, 0.26)
    steer.remember(state.targetX, 0.0, 0.0, "Sprint", False)
    assert (steer.dirX, steer.dirY) == (1.0, 0.0)
    state.remoteVelocityX, state.remoteVelocityY = -0.001, 0.0
    assert not steer.shouldReissue(current, state.targetX, 0.0, 0.0, "Sprint", False, 0.26)
    steer.reset()
    state.remoteVelocityX, state.remoteVelocityY = 0.0, 0.0
    steer.remember(8.0, 0.0, 0.0, "Sprint", False)
    assert (steer.dirX, steer.dirY) == (0.0, 0.0)


def test_v2_world_flags_cosmetic_car_and_departure():
    lua = receiver()
    welcome(lua)
    movement(lua, flags=16, vehicle=35, source="vehicle")
    sync = upvalue(lua, "Sync")
    assert lua.globals().carPose is None  # native timeline is still on foot
    lua.globals().sampleText = "interpolated 8 2 3 270 0 3 0 0 10 16 255 120 0"
    movement(lua, sequence=2, flags=16, vehicle=35, source="vehicle")
    # Make accidental use of the v1 extrapolator fail loudly.
    lua.execute("function noV1Pose() error('v1 pose extrapolation on v2') end")
    sync.extrapolateRemotePose = lua.globals().noV1Pose
    frame(lua, 3)
    car = lua.globals().carPose
    assert car is not None and (car.x, car.y, car.z, car.index) == (8, 2, 3, 35)
    enqueue(lua, "2|16|C3E1|100|1|612")  # host time 100 * 3 minutes
    lua.globals().sampleText = "interpolated 8 2 3 270 0 3 0 0 4 1 255 120 0"
    movement(lua, sequence=3, flags=1)
    assert sync.remoteTimeMinutes == 300 and sync.remoteFlags == 1
    assert lua.globals().carPose is None
    # A peer leaving while its cosmetic car exists must despawn that entity
    # before resetting vehicleShown/index bookkeeping.
    lua.globals().sampleText = "interpolated 8 2 3 270 0 3 0 0 10 16 255 120 0"
    movement(lua, sequence=4, flags=16, vehicle=35, source="vehicle")
    assert lua.globals().carPose is not None
    enqueue(lua, "0|0|peer_leave 2 disconnected")
    frame(lua)
    assert sync.v2Sample is None and lua.globals().markerPose is None
    assert lua.globals().npc is None and sync.isV2()
    assert lua.globals().carPose is None and not sync.vehicleShown
    assert lua.globals().legacyPushes == 0


def test_auto_fallback_and_explicit_error_never_replays_old_slot():
    lua = receiver(mode="auto")
    g = lua.globals()
    assert g.legacyPushes == 0
    g.net.has, g.net.seq, g.net.x = True, 900, 99
    enqueue(lua, "0|0|no_answer")
    frame(lua, 4)
    assert not upvalue(lua, "Sync").isV2()
    assert g.legacyPushes > 0 and g.markerPose is None
    assert upvalue(lua, "Diag").packetsReceived == 0
    assert not g.combatSuppressed
    lua = receiver()
    enqueue(lua, "0|0|rejected bad_key")
    frame(lua, 20)
    assert upvalue(lua, "Sync").isV2() and lua.globals().legacyPushes == 0


def test_role_unload_reload_shutdown_own_native_lifecycle():
    lua = receiver()
    welcome(lua)
    movement(lua)
    g = lua.globals()
    upvalue(lua, "Diag").roleChanged = True
    frame(lua)
    assert g.nativeStops == 1 and g.nativeConnections == 2 and g.markerPose is None
    g.preGame = True
    frame(lua)
    assert g.nativeStops == 2
    g.preGame = False
    frame(lua)
    assert g.nativeConnections == 3
    g.events["onShutdown"]()
    assert g.nativeStops == 3 and not g.combatSuppressed


if __name__ == "__main__":
    failed = 0
    for test in (test_native_sample_is_not_a_packet_or_second_prediction,
                 test_native_buffered_target_does_not_reverse_steering_direction,
                 test_v2_world_flags_cosmetic_car_and_departure,
                 test_auto_fallback_and_explicit_error_never_replays_old_slot,
                 test_role_unload_reload_shutdown_own_native_lifecycle):
        try:
            test()
            print(f"PASS  {test.__name__}")
        except Exception as error:
            import traceback
            traceback.print_exc()
            print(f"FAIL  {test.__name__}: {error!r}")
            failed += 1
    sys.exit(failed)
