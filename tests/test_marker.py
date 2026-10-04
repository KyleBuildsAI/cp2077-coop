"""Partner marker integration: movement truth, combat isolation and lifecycle.

Runs the real receive loop with a marker bridge mock. The separate redscript
compile group checks the actual native signatures; icon rendering needs a game.
"""
import sys

import test_live_bugs as live
import test_timing as timing


MARKER_MOCK = r"""
markerPose = nil
markerCalls = {}
markerHides = 0
markerFails = false
preGame = false
playerGone = false
Game.GetPlayer = function() if not playerGone then return player end end
Game.GetSystemRequestsHandler = function()
    return { IsPreGame = function() return preGame end }
end
function player:CP2077Coop_ShowRemoteMarker(x, y, z)
    markerCalls[#markerCalls + 1] = { t = simTime, x = x, y = y, z = z }
    if markerFails then error("marker system unavailable") end
    markerPose = { x = x, y = y, z = z }
    return true
end
function player:CP2077Coop_HideRemoteMarker()
    markerHides = markerHides + 1
    markerPose = nil
end
"""


def receiver(role="host"):
    lua = live.make_receiver(role=role, extra=MARKER_MOCK)
    # First frame creates the session before we put a packet in the DLL slot.
    lua.globals().events["onUpdate"](1 / 60)
    return lua


def packet(lua, sequence, x, y, z=0.0, combat=False):
    g = lua.globals()
    g.net.has, g.net.seq = True, sequence
    g.net.x, g.net.y, g.net.z = x, y, z
    g.net.fx, g.net.fy = (12.0, 9999.0) if combat else (0.0, 1.0)
    frame(lua)


def frame(lua, delta=1 / 60):
    g = lua.globals()
    g.simTime += delta
    g.events["onUpdate"](delta)


def upvalue(lua, name):
    lua.execute(timing.FIND_UPVALUE)
    return lua.eval("findUpvalue")(lua.globals().events["onUpdate"], name)


def test_remote_truth_and_far_partner():
    for role in ("host", "joiner"):
        lua = receiver(role)
        # No avatar can spawn 2 km away. The marker must still show the peer,
        # including the right floor, before the joiner's teleport settles.
        packet(lua, 1, 2000.0, -500.0, 62.0)
        pose = lua.globals().markerPose
        assert pose is not None and (pose.x, pose.y, pose.z) == (2000.0, -500.0, 62.0)
        for seq in range(2, 50):
            packet(lua, seq, 2000.0 + seq, -500.0, 62.0)
        pose = lua.globals().markerPose
        assert pose.x == 2049.0 and lua.globals().npc is None
        assert len(lua.globals().markerCalls) == 49
        lua.globals().events["onDraw"]()  # exact position + distance rows


def test_stale_combat_and_reconnect():
    lua = receiver()
    packet(lua, 1, 25.0, 10.0)
    for seq in range(2, 123):
        # Hit coordinates are not the remote player's position. Even while
        # these arrive, 1.5 s without player movement data must expire the pin.
        packet(lua, seq, 999.0, -888.0, 300.0, combat=True)
    assert lua.globals().markerPose is None
    assert len(lua.globals().markerCalls) == 1
    packet(lua, 123, 42.0, 11.0)
    assert lua.globals().markerPose.x == 42.0
    lua.globals().net.has = False
    frame(lua)
    assert lua.globals().markerPose is None
    # The plugin can retain its last packet after disconnect. It is not new.
    lua.globals().net.has = True
    frame(lua)
    assert lua.globals().markerPose is None
    packet(lua, 124, 44.0, 12.0)
    assert lua.globals().markerPose.x == 44.0
    for _ in range(100):
        frame(lua)
    assert lua.globals().markerPose is None


def test_reset_unload_and_shutdown():
    lua = receiver()
    packet(lua, 1, 25.0, 10.0)
    upvalue(lua, "Diag").roleChanged = True
    frame(lua)
    assert lua.globals().markerPose is None
    frame(lua)  # held slot must not recreate it
    assert lua.globals().markerPose is None
    packet(lua, 2, 26.0, 10.0)
    lua.globals().preGame = True
    lua.globals().playerGone = True
    frame(lua)
    assert lua.globals().markerPose is None  # retained owner cleanup
    lua.globals().events["onDraw"]()  # nil player while panel draws
    lua.globals().preGame = False
    lua.globals().playerGone = False
    frame(lua)
    packet(lua, 3, 27.0, 10.0)
    assert lua.globals().markerPose is not None
    lua.globals().events["onShutdown"]()
    assert lua.globals().markerPose is None
    lua.globals().events["onShutdown"]()  # safe when already removed


def test_optional_bridge_failure_and_retry():
    lua = receiver()
    lua.globals().markerFails = True
    for seq in range(1, 121):
        packet(lua, seq, 25.0, 10.0)
    assert len(lua.globals().markerCalls) <= 2  # no error storm
    lua.globals().markerFails = False
    for seq in range(121, 251):
        packet(lua, seq, 26.0, 10.0)
    assert lua.globals().markerPose is not None
    # Missing new redscript must not break existing movement on a partial deploy.
    lua = receiver()
    lua.execute("player.CP2077Coop_ShowRemoteMarker = nil")
    for seq in range(1, 121):
        packet(lua, seq, 25.0, 10.0)
    assert lua.globals().markerPose is None
    assert upvalue(lua, "S").previousRemoteX == 25.0
    lua.globals().events["onDraw"]()


if __name__ == "__main__":
    failed = 0
    for test in (test_remote_truth_and_far_partner, test_stale_combat_and_reconnect,
                 test_reset_unload_and_shutdown, test_optional_bridge_failure_and_retry):
        try:
            test()
            print(f"PASS  {test.__name__}")
        except Exception as error:
            import traceback
            traceback.print_exc()
            print(f"FAIL  {test.__name__}: {error!r}")
            failed += 1
    sys.exit(failed)
