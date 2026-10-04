"""Actual NPC coordinator + single actor harness + actual native adapter, mocked game/socket.

No game writes. Assertions concern opt-in, epochs, ownership and lifecycle, not AI fidelity.
"""
from pathlib import Path
import sys

import test_net_transport as transport
import test_v2_integration as integration

MOD = transport.MODULE.parent


def modules(lua):
    for name in ("testnpc", "npc_test"):
        lua.execute("package.preload['" + name + "']=function()\n" +
                    (MOD / (name + ".lua")).read_text(encoding="utf-8") + "\nend")
    lua.execute("Npc = require('npc_test')")


SETUP = r'''
function entity()
    local e={spawns=0,clears=0,moves=0}
    e.clear=function() e.clears=e.clears+1; if not e.holdDeletion then e.pose=nil end end
    e.spawn=function(p) e.spawns=e.spawns+1; e.pose={x=p.x,y=p.y,z=p.z,yaw=p.yaw}; return true end
    e.read=function() return e.pose end
    e.move=function(p) e.moves=e.moves+1; e.pose={x=p.x,y=p.y,z=p.z,yaw=p.yaw}; return true end
    return e
end
eh,ej=entity(),entity()
serial=100000
function fresh() serial=serial+1; return tostring(serial) end
function makeHost(enabled) return Npc.new({enabled=enabled,transport=a,entity=eh,epochFactory=fresh}) end
function makeJoiner(enabled) return Npc.new({enabled=enabled,transport=b,entity=ej,epochFactory=fresh}) end
function run(count, pausedHost, pausedJoiner)
    for i=1,count do
        a:update(.05); a:push(state(10))
        b:update(.05); b:push(state(40))
        while a:takePacket() do end
        while b:takePacket() do end
        while a:takePayload() do end
        while b:takePayload() do end
        nh:update(.05,pausedHost); nj:update(.05,pausedJoiner)
    end
end
function start(host,joiner)
    pair()
    nh,nj=makeHost(host),makeJoiner(joiner)
    run(40)
end
function active()
    start(true,true)
    assert(nh:status().ready and nj:status().ready)
    assert(nh:spawnNear({x=100,y=200,z=3}))
    run(40)
    assert(eh.spawns==1 and ej.spawns==1 and ej.pose.x==94)
end
'''


def runtime():
    lua = transport.runtime()
    modules(lua)
    lua.execute(SETUP)
    return lua


def test_disabled_and_one_sided_optin_cannot_create_actors_or_extra_poll():
    runtime().execute(r'''
        local off=Npc.new()
        off:update(1); off:shutdown(); assert(off:status().state=='off')
        start(true,false)
        assert(not nh:status().ready and not nj:status().ready)
        assert(eh.spawns==0 and ej.spawns==0 and eh.clears==0 and ej.clears==0)
        local count=#bus.calls
        nh:update(0); nj:update(0)
        assert(#bus.calls==count) -- coordinator never calls Net_Poll or native sampling
    ''')


def test_handshake_and_host_only_west_lane_actor_path():
    runtime().execute(r'''
        active()
        assert(not nj:spawnNear({x=0,y=0,z=0}))
        assert(nh:togglePath())
        run(120)
        assert(ej.pose.x==94 and ej.pose.y>200 and ej.pose.y<=212)
        assert(eh.spawns==1 and ej.spawns==1)
        assert(nh:remove()); run(30)
        assert(eh.pose==nil and ej.pose==nil)
    ''')


def test_handshake_refused_sends_and_lost_ack_recover_without_duplicate_actor():
    runtime().execute(r'''
        pair()
        local sendA=bus.clients[1].native.Net_SendTo
        local sendB=bus.clients[2].native.Net_SendTo
        local refusals,drops=4,3
        bus.clients[1].native.Net_SendTo=function(peer,ch,msg)
            if ch==20 and refusals>0 then refusals=refusals-1; return false end
            return sendA(peer,ch,msg)
        end
        bus.clients[2].native.Net_SendTo=function(peer,ch,msg)
            if msg:sub(1,7)=='C3N1|A|' and drops>0 then drops=drops-1; return true end
            return sendB(peer,ch,msg)
        end
        nh,nj=makeHost(true),makeJoiner(true)
        run(160)
        assert(nh:status().ready and nj:status().ready)
        assert(nh:spawnNear({x=100,y=200,z=3})); run(40)
        assert(eh.spawns==1 and ej.spawns==1)
    ''')


def test_wrong_sessions_challenge_and_unsolicited_actor_messages_rejected():
    runtime().execute(r'''
        start(false,true)
        local c=b:extensionContext()
        for _,payload in ipairs({
            'C3N1|O|999|202|123456|'..nj.joinNonce,
            'C3N1|O|101|999|123456|'..nj.joinNonce,
            'C3N1|O|101|202|123456|999999',
            'NT1|123456|B|1|1|2|3|0'
        }) do bus.inject(2,'1|20|'..payload) end
        b:update(0); nj:update(0)
        assert(not nj:status().ready and ej.spawns==0 and ej.clears==0)
    ''')


def test_reactivation_uses_fresh_challenge_and_rejects_old_offer():
    runtime().execute(r'''
        active()
        local oldEpoch=nh.hostEpoch
        local offer='C3N1|O|101|202|'..oldEpoch..'|'..nj.joinNonce
        nj:shutdown(); nj=makeJoiner(true); nj:update(0)
        bus.inject(2,'1|20|'..offer); b:update(0); nj:update(0)
        assert(not nj:status().ready and ej.pose==nil)
        run(60)
        assert(nh:status().ready and nj:status().ready and nh.hostEpoch~=oldEpoch)
        assert(eh.pose==nil and ej.pose==nil)
    ''')


def test_generation_change_and_pause_remove_actor_then_renegotiate():
    runtime().execute(r'''
        active()
        local old=nh.hostEpoch
        a:enableExtensions(false); a:enableExtensions(true)
        run(60)
        assert(nh:status().ready and nj:status().ready and nh.hostEpoch~=old)
        assert(eh.pose==nil and ej.pose==nil)
        assert(nh:spawnNear({x=100,y=200,z=3})); run(40)
        run(5,true,false)
        assert(eh.pose==nil)
        run(70,false,false)
        assert(nh:status().ready and nj:status().ready and ej.pose==nil)
        assert(nh:spawnNear({x=100,y=200,z=3})); run(40)
        run(5,false,true); run(70,false,false)
        assert(eh.pose==nil and ej.pose==nil and nh:status().ready and nj:status().ready)
    ''')


def test_silence_timeout_and_overflow_fail_closed_without_player_disconnect():
    runtime().execute(r'''
        active()
        local send=bus.clients[1].native.Net_SendTo
        bus.clients[1].native.Net_SendTo=function(peer,ch,msg)
            if ch==2 then return true end
            return send(peer,ch,msg)
        end
        run(80)
        assert(ej.pose==nil and nj.actor.expired==1)
        for i=1,70 do bus.inject(2,'1|20|flood') end
        b:update(0); nj:update(0)
        assert(b:status().extensionFault and b:status().ready)
        run(30)
        assert(not nj:status().ready and b:status().extensionFault)
        assert(bus.clients[2].disconnects==0)
    ''')


def test_clean_shutdown_keeps_actor_tag_empty():
    runtime().execute(r'''
        active()
        nh:shutdown(); nj:shutdown()
        assert(eh.pose==nil and ej.pose==nil and not nh.enabled and not nj.enabled)
        assert(a:extensionContext()==nil and b:extensionContext()==nil)
        assert(a:status().ready and b:status().ready)
    ''')


def test_overflow_on_menu_frame_stays_disabled_after_resume():
    runtime().execute(r'''
        active()
        for i=1,70 do bus.inject(2,'1|20|flood') end
        b:update(0); nj:update(.05,true)
        assert(nj.fault and ej.pose==nil)
        run(40,false,false)
        assert(nj.fault and b:status().extensionFault and not b:extensionContext())
        assert(not nj:status().ready and b:status().ready)
    ''')


def test_async_engine_removal_is_not_acknowledged_until_owned_actor_disappears():
    runtime().execute(r'''
        active()
        ej.holdDeletion=true
        ej.tagged=true
        local clear=ej.clear
        ej.clear=function() ej.tagged=false; clear() end
        ej.exists=function() return ej.pose~=nil end -- population survives tag removal
        assert(nh:remove()); run(20)
        assert(not ej.tagged and ej.pose and nh.actor.actor and nh.actor.actor.stopping)
        assert(nj.actor.pendingDeleteAck==1)
        ej.holdDeletion=false
        run(20)
        assert(ej.pose==nil and nh.actor.actor==nil and nj.actor.pendingDeleteAck==nil)
        assert(nh:spawnNear({x=110,y=200,z=3})); run(30)
        assert(ej.pose.x==104 and ej.spawns==2)
    ''')


def test_registered_origin_handle_waits_for_real_initial_placement_before_ack_or_move():
    runtime().execute(r'''
        start(true,true)
        ej.spawn=function(p)
            ej.spawns=ej.spawns+1
            ej.spawnPose={x=p.x,y=p.y,z=p.z,yaw=p.yaw}
            ej.pose={x=0,y=0,z=0,yaw=0} -- registered handle, engine has not placed it
            return true
        end
        assert(nh:spawnNear({x=100,y=200,z=3})); run(20)
        assert(ej.spawns==1 and ej.moves==0 and not nh.actor.actor.ack)
        assert(not nj.actor.actor.placed and nj:status().text:find('initial placement',1,true))
        assert(nh:togglePath()); run(50) -- latest target moves >2 m from original spawn
        assert(ej.moves==0 and not nh.actor.actor.ack)
        ej.pose=ej.spawnPose
        run(10)
        assert(nj.actor.actor.placed and nh.actor.actor.ack and ej.moves>0)
        assert(ej.pose.y>ej.spawnPose.y+2) -- initial readiness used immutable spawn pose
    ''')


def test_origin_ghost_that_never_places_times_out_despite_fresh_state_packets():
    runtime().execute(r'''
        start(true,true)
        ej.spawn=function(p)
            ej.spawns=ej.spawns+1; ej.pose={x=0,y=0,z=0,yaw=0}; return true
        end
        assert(nh:spawnNear({x=100,y=200,z=3})); run(140)
        assert(ej.pose==nil and ej.moves==0 and nj.actor.spawnFailures==1)
        assert(nj.actor.lastFailure=='initial attachment/placement timeout')
        assert(nh.actor.actor and not nh.actor.actor.ack)
        run(110)
        assert(nh.actor.actor==nil and nh.actor.spawnFailures==1)
    ''')


def test_cet_bridge_rejects_unattached_actor_but_checks_tracked_existence():
    runtime().execute(r'''
        local attached=false
        local actor={IsAttached=function() return attached end,IsDead=function() return false end,
            GetWorldPosition=function() return {x=20,y=30,z=5} end,GetWorldYaw=function() return 0 end}
        local player={CP2077Coop_TestNpcGet=function() return actor end,
            CP2077Coop_TestNpcExists=function() return true end}
        local bridge=require('testnpc').cetEntity(function() return player end)
        assert(bridge.read()==nil and bridge.exists())
        attached=true
        assert(bridge.read().x==20 and bridge.exists())
        player.CP2077Coop_TestNpcGet=function() return nil end -- tag removed, owned ID still present
        assert(bridge.read()==nil and bridge.exists())
    ''')


def test_previous_actor_cleanup_has_deadline_and_blocks_replacement():
    runtime().execute(r'''
        start(true,true)
        ej.pose={x=1,y=2,z=3,yaw=0}; ej.holdDeletion=true
        assert(nh:spawnNear({x=100,y=200,z=3})); run(140)
        assert(ej.spawns==0 and ej.pose and nj.actor.actor==nil)
        assert(nj.actor.lastFailure=='previous actor cleanup timeout')
        assert(not nh.actor.actor.ack)
        run(100)
        assert(nh.actor.actor.stopping and nj.actor.pendingDeleteAck==1)
        ej.holdDeletion=false; run(20)
        assert(nh.actor.actor==nil and ej.pose==nil)
    ''')


def test_spawn_audit_records_requested_placed_and_first_bind_once():
    runtime().execute(r'''
        start(true,true)
        local lines={}
        nh.actor.log=function(line) table.insert(lines,line) end
        assert(nh:spawnNear({x=100,y=200,z=3})); run(50)
        assert(#lines==3)
        assert(lines[1]:find('spawn_requested role=host',1,true))
        assert(lines[2]:find('spawn_placed role=host',1,true))
        assert(lines[3]:find('first_bind_sent role=host',1,true))
        for _,line in ipairs(lines) do assert(line:find('x=94.000 y=200.000 z=3.000',1,true)) end
        run(20); assert(#lines==3)
    ''')


def test_real_init_default_off_and_primary_controls_precede_diagnostics():
    lua = integration.receiver(extra=r'''
        Game.Net_NowMs=function() error('NPC clock called while disabled') end
        function player:CP2077Coop_TestNpcClear() error('NPC method called while disabled') end
    ''')

    integration.welcome(lua)
    integration.movement(lua)
    integration.frame(lua, 20)
    sync = integration.upvalue(lua, "Sync")
    assert sync.npcTest is None and not sync.transportConfig.npc_test
    lua.execute(r'''
        order={}
        ImGui.Button=function(label) table.insert(order,'button:'..label); return false end
        ImGui.TextColored=function(r,g,b,a,label) table.insert(order,'text:'..label) end
        events.onDraw()
        assert(order[1]=='button:Go to test area')
        assert(order[2]=='button:Teleport to host')
        assert(order[3]=='button:Log stats now')
    ''')


def opted_in_init(bridge=True):
    preload = ""
    for name in ("testnpc", "npc_test"):
        preload += "package.preload['" + name + "']=function()\n" + (MOD / (name + ".lua")).read_text(encoding="utf-8") + "\nend\n"
    mock = r'''
        Game.Net_NowMs=function() return 123456.789 end
        npcClears,npcSpawns=0,0
        npcPose=nil
        function player:CP2077Coop_TestNpcClear() npcClears=npcClears+1; npcPose=nil end
        function player:CP2077Coop_TestNpcExists() return npcPose~=nil end
        function player:CP2077Coop_TestNpcSpawn(x,y,z,yaw)
            npcSpawns=npcSpawns+1; npcPose={x=x,y=y,z=z,yaw=yaw}; return true
        end
        function player:CP2077Coop_TestNpcMove(x,y,z,yaw) npcPose={x=x,y=y,z=z,yaw=yaw}; return true end
        function player:CP2077Coop_TestNpcGet()
            if not npcPose then return nil end
            return {IsAttached=function() return true end, IsDead=function() return false end,
                GetWorldPosition=function() return npcPose end, GetWorldYaw=function() return npcPose.yaw end}
        end
    ''' if bridge else "Game.Net_NowMs=function() return 123456.789 end"
    lua = integration.receiver(mode="v2\nnpc_test=true", extra=preload + mock)
    integration.welcome(lua)
    integration.movement(lua)
    sync = integration.upvalue(lua, "Sync")
    session = sync.transport.status(sync.transport).session
    integration.enqueue(lua, f"2|20|C3N1|J|{session}|100|999999")
    integration.frame(lua, 3)
    epoch = sync.npcTest.hostEpoch
    assert epoch and not sync.npcTest.status(sync.npcTest).ready
    integration.enqueue(lua, f"2|20|C3N1|A|{session}|100|{epoch}|999999")
    integration.frame(lua, 3)
    return lua, sync


def test_real_init_optin_and_save_unload_cleanup():
    lua, sync = opted_in_init()
    assert sync.npcTest.status(sync.npcTest).ready
    assert sync.npcTest.spawnNear(sync.npcTest, lua.table_from(dict(x=100, y=200, z=3)))
    integration.frame(lua, 5)
    assert lua.globals().npcSpawns == 1 and lua.globals().npcPose.x == 94
    lua.globals().preGame = True
    integration.frame(lua)
    assert sync.npcTest is None and lua.globals().npcPose is None
    assert lua.globals().npcClears >= 2


def test_missing_actor_bridge_disables_only_npc_extension():
    lua, sync = opted_in_init(bridge=False)
    assert sync.npcTest is None and sync.npcTestError
    before = lua.globals().nativePushes
    integration.frame(lua, 10)
    assert lua.globals().nativePushes > before
    assert sync.transport.status(sync.transport).ready


def main():
    failures = 0
    for name, function in list(globals().items()):
        if name.startswith("test_") and callable(function):
            try:
                function()
                print("PASS", name)
            except Exception as error:
                failures += 1
                print("FAIL", name, error)
    return failures


if __name__ == "__main__":
    sys.exit(main())
