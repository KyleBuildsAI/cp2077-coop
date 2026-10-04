"""Exercise the actual LuaJIT harness with asynchronous, failure-prone entity/transport callbacks.

These tests prove protocol/lifecycle behavior, not Cyberpunk AI or render fidelity.
Install harness/requirements-test.txt, then: python -m pytest tests -q
"""
from pathlib import Path

import pytest
from lupa.luajit21 import LuaRuntime


SOURCE = Path(__file__).parents[1] / "harness" / "lua" / "testnpc.lua"


SETUP = r'''
local Harness = ...
local function entity()
    local e = {spawns=0, clears=0, moves=0, ready=true, accept=true}
    e.spawn = function(p)
        e.spawns = e.spawns + 1
        if not e.accept then return false end
        if e.ready then e.pose = {x=p.x, y=p.y, z=p.z, yaw=p.yaw} end
        return true
    end
    e.read = function() return e.pose end
    e.move = function(p)
        e.moves = e.moves + 1
        if not e.pose then return false end
        e.pose = {x=p.x, y=p.y, z=p.z, yaw=p.yaw}
        return true
    end
    e.clear = function() e.clears = e.clears + 1; if not e.holdDeletion then e.pose = nil end end
    return e
end
local env = {out={}, blocked=false, h=entity(), j=entity()}
local function sender(id)
    return function(reliable, message)
        if env.blocked then return false end
        table.insert(env.out, {sender=id, reliable=reliable, message=message})
        return true
    end
end
env.host = Harness.new({enabled=true, role="host", epoch="123456", peer=42, entity=env.h, send=sender(41)})
env.joiner = Harness.new({enabled=true, role="joiner", epoch="123456", peer=41, entity=env.j, send=sender(42)})
env.deliver = function(dropKind)
    local queue = env.out
    env.out = {}
    for _, packet in ipairs(queue) do
        if not dropKind or not packet.message:find("|" .. dropKind .. "|", 1, true) then
            local target = packet.sender == 41 and env.joiner or env.host
            target:receive(packet.sender, packet.reliable, packet.message)
        end
    end
end
env.tick = function(dt, dropKind)
    env.host:update(dt)
    env.joiner:update(dt)
    env.deliver(dropKind)
end
return env
'''


@pytest.fixture
def bench():
    lua = LuaRuntime(unpack_returned_tuples=True)
    harness = lua.execute(SOURCE.read_text())
    return lua, harness, lua.execute(SETUP, harness)


def pose(lua, x=1, y=2, z=3, yaw=45):
    return lua.table_from(dict(x=x, y=y, z=z, yaw=yaw))


def ticks(env, count=30, drop=None):
    for _ in range(count):
        env.tick(0.05, drop)


def active(bench):
    lua, harness, env = bench
    assert env.host.spawn(env.host, pose(lua))
    ticks(env)
    assert env.host.actor.ack
    assert env.j.pose.x == 1
    return lua, harness, env


def test_disabled_by_default_has_no_side_effects(bench):
    lua, harness, env = bench
    disabled = harness.new(lua.table_from(dict(entity=env.h)))
    assert not disabled.spawn(disabled, pose(lua))
    disabled.update(disabled, 100)
    disabled.shutdown(disabled)
    assert env.h.clears == 1  # only the enabled fixture's initialization
    assert env.h.spawns == 0


def test_spawn_and_bounded_movement_use_actual_entity_samples(bench):
    lua, _, env = active(bench)
    for frame in range(100):
        env.host.move(env.host, pose(lua, x=1 + frame / 20))
        env.tick(0.05, None)
    ticks(env, 10)
    assert env.j.pose.x == pytest.approx(env.h.pose.x, abs=0.002)
    assert env.h.spawns == env.j.spawns == 1
    assert env.host.sent < 85  # ~10 Hz snapshots, bounded bind retries


def test_rejected_send_retries_spawn_until_accepted(bench):
    lua, _, env = bench
    env.blocked = True
    assert env.host.spawn(env.host, pose(lua))
    ticks(env, 20)
    assert env.joiner.actor is None
    env.blocked = False
    ticks(env, 30)
    assert env.host.actor.ack
    assert env.j.spawns == 1


@pytest.mark.parametrize("kind", ["B", "A"])
def test_missing_bind_or_ack_retries_without_duplicate_actor(bench, kind):
    lua, _, env = bench
    env.host.spawn(env.host, pose(lua))
    ticks(env, 20, kind)
    assert not env.host.actor.ack
    ticks(env, 25)
    assert env.host.actor.ack
    assert env.j.spawns == 1


def test_duplicate_bind_does_not_rewind_actor(bench):
    lua, _, env = active(bench)
    env.host.move(env.host, pose(lua, x=7))
    ticks(env, 10)
    before = env.j.spawns
    assert env.joiner.receive(env.joiner, 41, True, "NT1|123456|B|1|1|2|3|45")
    assert env.joiner.actor.target.x == 7
    ticks(env, 5)
    assert env.j.spawns == before


@pytest.mark.parametrize("sender,reliable,message", [
    (99, True, "NT1|123456|B|1|1|2|3|45"),
    (41, True, "NT1|111111|B|1|1|2|3|45"),
    (41, False, "NT1|123456|B|1|1|2|3|45"),
    (41, 1, "NT1|123456|B|1|1|2|3|45"),
    (41, True, "NT1|123456|B|1|nan|2|3|45"),
    (41, True, "NT1|123456|B|1|100001|2|3|45"),
    (41, True, "NT1|123456|B|1|1|2|3|45|extra"),
    (41, True, "NT1|123456|B|-1|1|2|3|45"),
    (41, True, "NT1|123456|B|1.5|1|2|3|45"),
    (41, True, "x" * 257),
])
def test_untrusted_or_malformed_spawn_cannot_create_actor(bench, sender, reliable, message):
    _, _, env = bench
    assert not env.joiner.receive(env.joiner, sender, reliable, message)
    ticks(env)
    assert env.j.spawns == 0


def test_joiner_cannot_create_or_move_host_actor(bench):
    lua, _, env = bench
    assert not env.joiner.spawn(env.joiner, pose(lua))
    assert not env.host.receive(env.host, 42, True, "NT1|123456|B|1|1|2|3|45")
    assert env.host.actor is None


def test_reordered_and_wrong_channel_state_does_not_rewind_pose(bench):
    _, _, env = active(bench)
    assert env.joiner.receive(env.joiner, 41, False, "NT1|123456|S|1|200|9|2|3|45")
    assert not env.joiner.receive(env.joiner, 41, False, "NT1|123456|S|1|199|1|2|3|45")
    assert not env.joiner.receive(env.joiner, 41, True, "NT1|123456|S|1|201|1|2|3|45")
    assert env.joiner.actor.target.x == 9


def test_stale_actor_expires_and_old_bind_cannot_resurrect_it(bench):
    _, _, env = active(bench)
    ticks(env, 70, "S")
    assert env.joiner.actor is None and env.j.pose is None
    assert env.joiner.expired == 1
    assert not env.joiner.receive(env.joiner, 41, True, "NT1|123456|B|1|1|2|3|45")


def test_stop_retries_and_tombstone_blocks_late_packets(bench):
    lua, _, env = active(bench)
    assert env.host.stop(env.host)
    ticks(env, 10, "D")
    assert env.host.actor.stopping
    ticks(env, 10, "X")
    assert env.joiner.actor is None
    ticks(env, 10)
    assert env.host.actor is None
    assert not env.joiner.receive(env.joiner, 41, True, "NT1|123456|B|1|1|2|3|45")
    assert env.host.spawn(env.host, pose(lua, x=9))
    ticks(env, 20)
    assert env.joiner.actor.id == 2 and env.j.pose.x == 9


def test_spawn_accepted_but_never_attaches_expires(bench):
    lua, _, env = bench
    env.h.ready = False
    env.host.spawn(env.host, pose(lua))
    ticks(env, 130)
    assert env.host.actor is None
    assert env.h.pose is None and env.j.spawns == 0


def test_joiner_never_attaches_causes_host_to_stop(bench):
    lua, _, env = bench
    env.j.ready = False
    env.host.spawn(env.host, pose(lua))
    ticks(env, 250)
    assert env.host.actor is None and env.joiner.actor is None


def test_entity_not_ready_retries_spawn_request(bench):
    lua, _, env = bench
    env.h.accept = False
    env.host.spawn(env.host, pose(lua))
    ticks(env, 10)
    assert env.h.pose is None
    env.h.accept = True
    ticks(env)
    assert env.host.actor.ack


def test_shutdown_clears_and_disables_inbound_processing(bench):
    _, _, env = active(bench)
    env.host.shutdown(env.host)
    env.joiner.shutdown(env.joiner)
    assert env.h.pose is None and env.j.pose is None
    assert not env.joiner.receive(env.joiner, 41, True, "NT1|123456|B|2|1|2|3|45")


def test_async_removal_ack_waits_for_entity_to_disappear(bench):
    lua, _, env = active(bench)
    env.j.holdDeletion = True
    lua.execute('''local e=...; e.j.tagged=true; local clear=e.j.clear
      e.j.clear=function() e.j.tagged=false; clear() end
      e.j.exists=function() return e.j.pose~=nil end''', env)
    assert env.host.stop(env.host)
    ticks(env, 20)
    assert not env.j.tagged and env.j.pose is not None and env.host.actor.stopping
    assert env.joiner.pendingDeleteAck == 1
    env.j.holdDeletion = False
    ticks(env, 20)
    assert env.j.pose is None and env.host.actor is None
    assert env.joiner.pendingDeleteAck is None
    assert env.host.spawn(env.host, pose(lua, x=9))
    ticks(env, 20)
    assert env.j.pose.x == 9 and env.j.spawns == 2


def test_new_incarnation_waits_for_previous_tag_to_clear(bench):
    lua, _, env = bench
    env.h.pose = pose(lua, x=99)
    env.h.holdDeletion = True
    assert env.host.spawn(env.host, pose(lua, x=1))
    ticks(env, 20)
    assert env.h.spawns == 0 and env.j.spawns == 0
    env.h.holdDeletion = False
    ticks(env, 30)
    assert env.h.spawns == 1 and env.j.spawns == 1 and env.j.pose.x == 1


def test_missing_epoch_and_peer_are_configuration_errors(bench):
    lua, harness, _ = bench
    with pytest.raises(Exception, match="epoch"):
        harness.new(lua.table_from(dict(enabled=True, role="host")))
    with pytest.raises(Exception, match="peer"):
        harness.new(lua.table_from(dict(enabled=True, role="host", epoch="123456")))


def test_registered_origin_waits_for_initial_pose_before_move_and_ack(bench):
    lua, _, env = bench
    lua.execute('''local e = ...; e.j.spawn = function(p)
      e.j.spawns=e.j.spawns+1; e.j.pose={x=0,y=0,z=0,yaw=0}; return true
    end''', env)
    env.host.spawn(env.host, pose(lua, x=100))
    ticks(env, 20)
    assert env.joiner.actor is not None and not env.joiner.actor.placed
    assert not env.host.actor.ack and env.j.moves == 0
    env.host.move(env.host, pose(lua, x=110))
    ticks(env, 20)
    assert env.joiner.actor.target.x == 110
    assert env.joiner.actor.spawnPose.x == 100
    assert env.j.moves == 0 and not env.host.actor.ack
    env.j.pose = pose(lua, x=100)
    ticks(env, 10)
    assert env.joiner.actor.placed and env.host.actor.ack
    assert env.j.pose.x == 110 and env.j.spawns == 1


def test_finite_origin_ghost_expires_despite_fresh_network_states(bench):
    lua, _, env = bench
    lua.execute('''local e = ...; e.j.spawn = function(p)
      e.j.spawns=e.j.spawns+1; e.j.pose={x=0,y=0,z=0,yaw=0}; return true
    end''', env)
    env.host.spawn(env.host, pose(lua, x=100))
    ticks(env, 130)
    assert env.joiner.actor is None and env.j.pose is None
    assert env.joiner.spawnFailures == 1 and env.j.moves == 0
    assert env.joiner.lastFailure == "initial attachment/placement timeout"
    ticks(env, 120)
    assert env.host.actor is None and env.h.pose is None
    assert env.host.spawnFailures == 1


def test_cet_entity_requires_attachment_but_cleanup_tracks_tag(bench):
    lua, harness, _ = bench
    lua.execute('''local H=...
      local a={attached=false}
      function a:IsAttached() return self.attached end
      function a:IsDead() return false end
      function a:GetWorldPosition() return {x=100,y=2,z=3} end
      function a:GetWorldYaw() return 45 end
      local player={CP2077Coop_TestNpcGet=function() return a end,
        CP2077Coop_TestNpcExists=function() return true end}
      local bridge=H.cetEntity(function() return player end)
      assert(bridge.exists() and bridge.read()==nil)
      a.attached=true
      assert(bridge.read().x==100)
      player.CP2077Coop_TestNpcGet=function() return nil end
      assert(bridge.read()==nil and bridge.exists())
    ''', harness)


def test_cleanup_timeout_does_not_allow_overlapping_actor_or_false_ack(bench):
    lua, _, env = bench
    env.j.pose = pose(lua, x=99)
    env.j.holdDeletion = True
    env.host.spawn(env.host, pose(lua))
    ticks(env, 130)
    assert env.j.spawns == 0 and env.j.pose.x == 99
    assert env.joiner.actor is None
    assert env.joiner.lastFailure == "previous actor cleanup timeout"
    ticks(env, 120)
    assert env.host.actor.stopping and env.joiner.pendingDeleteAck == 1
    env.j.holdDeletion = False
    ticks(env, 20)
    assert env.j.pose is None and env.host.actor is None


def test_optional_spawn_audit_is_bounded_to_transition_events(bench):
    lua, _, env = bench
    lua.execute('''local e=...; e.audit={}
      e.host.log=function(line) table.insert(e.audit,line) end''', env)
    env.host.spawn(env.host, pose(lua, x=100))
    ticks(env, 60)
    assert len(env.audit) == 3
    assert "spawn_requested role=host" in env.audit[1]
    assert "spawn_placed role=host" in env.audit[2]
    assert "first_bind_sent role=host" in env.audit[3]
    assert all("x=100.000" in env.audit[i] for i in (1, 2, 3))


def test_idle_pose_queues_nothing_and_motion_is_paced(bench):
    lua, _, env = active(bench)
    assert env.h.moves == env.j.moves == 0
    lua.execute('''local e=...; e.moveTimes={}; local move=e.h.move
      e.h.move=function(p) table.insert(e.moveTimes,e.host.now); return move(p) end''', env)
    for i in range(400):
        env.host.move(env.host, pose(lua, x=1+i*.02))
        env.tick(.005, None)
    assert 18 <= len(env.moveTimes) <= 21
    assert all(env.moveTimes[i]-env.moveTimes[i-1] >= .099999 for i in range(2,len(env.moveTimes)+1))
    ticks(env, 10)
    before = env.h.moves
    ticks(env, 20)
    assert env.h.moves == before


def test_stuck_move_cancel_waits_for_terminal_then_uses_latest_target(bench):
    lua, _, env = active(bench)
    lua.execute('''local e=...; e.commandState=-1; e.cancels=0; e.requests={}
      e.h.moveState=function() return e.commandState end
      e.h.cancelMove=function() e.cancels=e.cancels+1 end
      e.h.move=function(p) table.insert(e.requests,p.x); e.commandState=1; return true end''', env)
    env.host.move(env.host, pose(lua, x=10)); ticks(env, 10)
    env.host.move(env.host, pose(lua, x=20)); ticks(env, 70)
    assert len(env.requests) == 1 and env.cancels == 1
    assert env.host.moveExpiries == 1 and env.j.pose.x == 1
    ticks(env, 30)
    assert len(env.requests) == 1 and env.cancels == 1
    env.commandState = 5
    ticks(env, 1)
    assert len(env.requests) == 2 and env.requests[2] == 20
    env.commandState = 3
    env.h.pose = pose(lua, x=20)
    ticks(env, 10)
    assert len(env.requests) == 2


def test_refused_moves_are_paced_and_do_not_replace_actual_network_pose(bench):
    lua, _, env = active(bench)
    lua.execute('''local e=...; e.attempts=0
      e.h.move=function() e.attempts=e.attempts+1; return false end''', env)
    env.host.move(env.host, pose(lua, x=20)); ticks(env, 20)
    assert 8 <= env.attempts <= 11
    assert env.host.moveRequests == 0 and env.j.pose.x == env.h.pose.x == 1


def test_shutdown_cancels_pending_movement_before_new_incarnation(bench):
    lua, _, env = active(bench)
    lua.execute('''local e=...; e.pending=false
      e.h.moveState=function() return e.pending and 1 or -1 end
      e.h.move=function() e.pending=true; return true end
      local clear=e.h.clear; e.h.clear=function() e.pending=false; clear() end''', env)
    env.host.move(env.host, pose(lua, x=20)); ticks(env, 10)
    assert env.pending
    env.host.shutdown(env.host)
    assert not env.pending and env.h.pose is None
