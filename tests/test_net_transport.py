"""Phase 3 adapter: two mocked native connections under the actual CET LuaJIT runtime.

Exercises transport ownership, application negotiation, legacy flag conversion,
queued raw metadata, independent native sampling, reconnect identity and fallback.
The native DLL's real socket/interpolation tests live in coopnet/dllproto.
"""
from pathlib import Path
import sys

from lupa.luajit21 import LuaRuntime

MODULE = Path(__file__).resolve().parents[1] / "bin/x64/plugins/cyber_engine_tweaks/mods/CP2077Coop/net_transport.lua"

MOCK = r"""
bus = {clients = {}, pushes = {}, sends = {}, calls = {}, clockReady = true}
function bus.inject(id, raw)
    table.insert(bus.clients[id].inbox, raw)
end
function bus.native(id, role)
    local c = {id=id, role=role, inbox={}, connected=false, disconnects=0}
    bus.clients[id] = c
    local n = {}
    n.Net_ConnectV2 = function(host, port, room, key, roleId)
        c.connected = true
        c.options = {host,port,room,key,roleId}
        bus.inject(id, '0|0|welcome '..id..' '..role)
        for otherId, other in pairs(bus.clients) do
            if otherId ~= id and other.connected then
                bus.inject(id, '0|0|peer_join '..otherId..' '..other.role)
                bus.inject(otherId, '0|0|peer_join '..id..' '..role)
            end
        end
        return true
    end
    n.Net_Disconnect = function()
        c.disconnects = c.disconnects + 1
        c.connected = false
    end
    n.Net_Poll = function()
        bus.calls[#bus.calls + 1] = {name='poll',id=id}
        if #c.inbox == 0 then return '' end
        return table.remove(c.inbox,1)
    end
    n.Net_SendTo = function(peer, channel, text)
        bus.sends[#bus.sends + 1] = {id=id,peer=peer,channel=channel,text=text}
        local other = bus.clients[peer]
        if not c.connected or not other or not other.connected then return false end
        bus.inject(peer, id..'|'..channel..'|'..text)
        return true
    end
    n.Net_PushPlayer = function(x,y,z,yaw,pitch,vx,vy,vz,move,flags,health)
        if not c.connected or not bus.clockReady then return false end
        bus.pushes[#bus.pushes+1] = {id=id,x=x,y=y,z=z,yaw=yaw,pitch=pitch,vx=vx,vy=vy,vz=vz,move=move,flags=flags,health=health}
        c.pose = string.format('interpolated %.3f %.3f %.3f %.2f %.2f %.2f %.2f %.2f %d %d %d 100.0 -80.0',
            x,y,z,yaw,pitch,vx,vy,vz,move,flags,health)
        return true
    end
    n.Net_SampleRemote = function(peer)
        bus.calls[#bus.calls+1] = {name='sample',id=id}
        if c.sampleOverride then return c.sampleOverride end
        local other=bus.clients[peer]
        return other and other.pose or ''
    end
    c.native = n
    return n
end
function state(x, flags, source, payload, vehicle)
    return {x=x or 0,y=20,z=30,fx=-1,fy=0,vx=3,vy=0,vz=0,flags=flags or 0,
        source=source or 'player',payload=payload,vehicleIndex=vehicle,moveState=2,health=230,pitch=5}
end
function pair()
    a = Net.new({mode='v2',role='host',natives=bus.native(1,'host'),probeDisabled=true,sessionEpoch='101',version='0.0.34'})
    b = Net.new({mode='v2',role='joiner',natives=bus.native(2,'joiner'),probeDisabled=true,sessionEpoch='202',version='0.0.34'})
    for i=1,8 do
        a:update(1/60); a:push(state(10,66))
        b:update(1/60); b:push(state(40,1))
    end
    assert(a:status().ready and b:status().ready)
    a:update(0); b:update(0) -- deliver the last joiner frame before resetting observations
    while a:takePacket() do end
    while b:takePacket() do end
    while a:takePayload() do end
    while b:takePayload() do end
end
function latestMovement(id)
    for i=#bus.sends,1,-1 do
        local s=bus.sends[i]
        if s.id==id and s.channel==1 then return s.text end
    end
end
"""


def runtime():
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().Net = lua.execute(MODULE.read_text(encoding="utf-8"))
    lua.execute(MOCK)
    return lua


def test_default_and_ownership():
    runtime().execute(r"""
        local calls=0
        local forbidden=setmetatable({}, {__index=function() calls=calls+1; error('v1 touched native') end})
        local v1=Net.new({natives=forbidden})
        v1:update(1); assert(not v1:isV2() and not v1:push(state()))
        v1:stop(); assert(calls==0)
        local auto=Net.new({mode='auto',natives={},probeDisabled=true})
        auto:update(0); assert(not auto:isV2() and auto:status().mode=='v1')
        local forced=Net.new({mode='v2',natives={},probeDisabled=true})
        assert(forced:isV2()); forced:update(0)
        assert(forced:isV2() and forced:status().mode=='error')
        local busy=Net.new({mode='v2',natives=forbidden})
        busy:update(0); assert(busy:status().reason:find('disable CoopNetCheck'))
        assert(calls==0)
    """)


def test_negotiation_queue_and_separate_sampling():
    runtime().execute(r"""
        pair()
        a:update(1/30); assert(a:push(state(12,66)))
        b:update(1/30)
        local p=b:takePacket()
        assert(p and p.x==12 and p.y==20 and p.z==30 and p.vx==3)
        assert(p.flags==322 and math.abs(p.fx+1)<1e-6 and p.fy==0)
        assert(p.sequence>0 and p.receivedAt>0 and p.peer==1)
        assert(b:takePacket()==nil)
        local s=b:sample()
        assert(s and s.mode=='interpolated' and s.x==12 and s.flags==322)
        assert(math.abs(s.fx+1)<1e-6 and math.abs(s.fy)<1e-6 and s.vx==3)
        assert(b:sample()==s and b:takePacket()==nil) -- no fake receive on render
        bus.inject(2,'1|1|'..latestMovement(1)) -- duplicate movement
        b:update(0); assert(b:takePacket()==nil)
        local count=#bus.pushes
        a:push(state(13)); assert(#bus.pushes==count) -- no duplicate push in a frame
        a:update(0.5); a:push(state(14)); b:update(0.5)
        local next=b:takePacket(); assert(next.sequence-p.sequence>=15) -- skipped time, no burst
    """)


def test_flags_extras_and_vehicle():
    runtime().execute(r"""
        for class=0,7 do
            for base=0,31 do
                local flags=base+class*32+256
                local native=Net.toNativeFlags(flags,false)
                assert(math.floor(native/32)%2==0) -- DRIVING must never be emitted
                assert(Net.toLegacyFlags(native,'host')==flags)
            end
        end
        pair()
        a:update(1/30); a:push(state(100,16+3*32,'vehicle',5*512+17,17))
        b:update(1/30)
        local p=b:takePacket()
        assert(p.source=='vehicle' and p.vehicleIndex==17 and p.flags==368)
        local extra=b:takePayload(); assert(extra.payload==2577 and extra.peer==1)
        assert(b:takePayload()==nil)
        local sample=b:sample(); assert(sample and sample.vehicleIndex==17 and sample.source=='vehicle')
        a:update(1/30); a:push(state(101,16,'vehicle',600,17))
        b:update(1/30); assert(b:takePayload().payload==600)
        a:update(1/30); a:push(state(102,2,'player',2))
        b:update(1/30); assert(b:takePayload()==nil) -- flags are not extras
    """)


def test_stale_native_and_raw_paths():
    runtime().execute(r"""
        pair(); assert(b:sample())
        bus.clients[2].sampleOverride='held 10 20 30 90 0 0 0 0 0 0 255 100 1500'
        b:update(0); assert(b:sample()==nil) -- native stream stale, raw still fresh
        bus.clients[2].sampleOverride=nil
        b:update(1.51); assert(b:sample()==nil) -- raw stream stale, native slot retained
        a:update(1.51); a:push(state(30)); b:update(0)
        assert(b:sample())
        local oldEpoch=b:status().epoch
        bus.inject(2,'0|0|peer_leave 1 quit'); b:update(0)
        assert(b:sample()==nil and b:takePacket()==nil and b:isV2())
        assert(b:status().epoch>oldEpoch)
        bus.inject(2,'1|1|'..latestMovement(1)); b:update(0)
        assert(b:takePacket()==nil) -- ghost snapshot cannot resurrect departed player
    """)


def test_reconnect_epochs_and_malformed_messages():
    runtime().execute(r"""
        pair()
        local old=latestMovement(1)
        local generation=b:status().epoch
        bus.inject(2,'0|0|peer_leave 1 quit')
        bus.inject(2,'0|0|peer_join 1 host')
        bus.inject(2,'1|30|C3H1|303|host|0.0.34')
        bus.inject(2,'1|1|'..old)
        b:update(0); assert(b:takePacket()==nil and b:sample()==nil)
        assert(b:status().epoch>generation)
        bus.inject(2,'1|1|C3M1|303|1|player|10|20|30|-1|0|3|0|0|2|2|230|5|-1|0')
        b:update(0)
        local p=b:takePacket(); assert(p and p.sequence==1 and p.reset)
        for _, text in ipairs({
            'C3M1|303|2|player|nan|20|30|-1|0|3|0|0|2|2|230|5|-1|0',
            'C3M1|303|2|player|10|20|30|0|0|3|0|0|2|2|230|5|-1|0',
            'C3M1|303|2|player|10|20|30|-1|0|3|0|0|999|2|230|5|-1|0',
            'C3M1|303|2|player|10|20|30|-1|0|3|0|0|2|2|230|5|-1',
            'C3M1|303|1|player|10|20|30|-1|0|3|0|0|2|2|230|5|-1|0'
        }) do bus.inject(2,'1|1|'..text) end
        bus.inject(2,'9|1|C3M1|303|2|player|10|20|30|-1|0|3|0|0|2|2|230|5|-1|0')
        b:update(0); assert(b:takePacket()==nil)
    """)


def test_fallback_rejection_and_slow_peer():
    runtime().execute(r"""
        local n=bus.native(1,'host')
        local auto=Net.new({mode='auto',natives=n,probeDisabled=true,noPeerAfter=0.5})
        auto:update(0); assert(auto:isV2())
        auto:update(0.6); assert(not auto:isV2() and bus.clients[1].disconnects==1)
        auto:update(10); assert(bus.clients[1].disconnects==1) -- no reconnect loop
        local forced=Net.new({mode='v2',natives=n,probeDisabled=true,noPeerAfter=0.5})
        forced:update(0); forced:update(10); assert(forced:isV2() and forced:status().mode=='probing')
        forced:stop()
        local blocked=Net.new({mode='auto',natives=n,probeDisabled=true})
        blocked:update(0); bus.inject(1,'0|0|rejected bad_key wrong room password')
        blocked:update(0); assert(blocked:isV2() and blocked:status().mode=='error')
        local unanswered=Net.new({mode='auto',natives=n,probeDisabled=true})
        unanswered:update(0); bus.inject(1,'0|0|no_answer'); unanswered:update(0)
        assert(not unanswered:isV2())
    """)


def test_teleport_survives_pacing_and_bad_calls():
    runtime().execute(r"""
        pair()
        a:update(1/30); a:push(state(10))
        a:update(0.001); a:push(state(1000)) -- discontinuity between send slots
        a:update(0.001); a:push(state(1000))
        a:update(1/30); a:push(state(1000))
        local p=bus.pushes[#bus.pushes]
        assert(math.floor(p.flags/32768)%2==1)
        assert(not a:push({x=0/0,y=0,z=0}))
        a.natives.Net_Poll=function() error('native unavailable') end
        a:update(0); assert(a:status().mode=='error' and a:sample()==nil)
    """)


def test_extension_opt_in_context_and_real_sender():
    runtime().execute(r"""
        pair()
        assert(a:extensionContext()==nil and b:extensionContext()==nil)
        bus.inject(2,'1|20|NT1|999|B|1|0|0|0|0'); b:update(0)
        assert(#b.extensionInbox==0) -- reserved traffic is inert by default
        a:enableExtensions(true); b:enableExtensions(true)
        local ac,bc=a:extensionContext(),b:extensionContext()
        assert(ac.peer==2 and ac.localRole=='host' and ac.localSession=='101' and ac.remoteSession=='202')
        assert(bc.peer==1 and bc.localRole=='joiner' and bc.localSession=='202' and bc.remoteSession=='101')
        assert(a:sendExtension(ac,20,'C3N1|101|202|999|offer'))
        b:update(0)
        local e=b:takeExtension(bc)
        assert(e and e.sender==1 and e.channel==20 and e.reliable and e.payload=='C3N1|101|202|999|offer')
        assert(e.context.generation==bc.generation and e.context.remoteSession=='101')
        -- A joiner ACK carries the HOST harness epoch, not the joiner's C3 sender epoch.
        assert(b:sendExtension(bc,20,'NT1|999|A|1'))
        a:update(0); e=a:takeExtension(ac)
        assert(e and e.sender==2 and e.reliable and e.payload=='NT1|999|A|1')
        assert(a:sendExtension(ac,2,'NT1|999|S|1|1|10|20|30|0'))
        b:update(0); e=b:takeExtension(bc)
        assert(e and not e.reliable and e.channel==2)
        bus.inject(2,'9|20|NT1|999|B|1|0|0|0|0')
        b:update(0); assert(b:takeExtension(bc)==nil) -- payload cannot assert its own identity
        assert(not a:sendExtension(ac,16,'bad') and not a:sendExtension(ac,1,'bad'))
        assert(not a:sendExtension(ac,20,string.rep('x',257)))
        assert(not a:sendExtension(ac,20,'bad\nmessage') and not a:sendExtension(ac,20,''))
        assert(a:takePacket()==nil and b:takePacket()==nil) -- no extension leaks to gameplay
    """)


def test_extension_context_expires_on_disable_and_peer_session_change():
    runtime().execute(r"""
        pair(); a:enableExtensions(true); b:enableExtensions(true)
        local ac,bc=a:extensionContext(),b:extensionContext()
        assert(a:sendExtension(ac,20,'NT1|999|D|1')); b:update(0)
        b:enableExtensions(false)
        assert(b:extensionContext()==nil and b:takeExtension(bc)==nil)
        assert(not b:sendExtension(bc,20,'NT1|999|X|1'))
        assert(b:isV2() and b:status().ready and b:sample())
        b:enableExtensions(true)
        local fresh=b:extensionContext()
        assert(fresh.extensionGeneration~=bc.extensionGeneration)
        assert(b:takeExtension(fresh)==nil and not b:sendExtension(bc,20,'old closure'))
        bc=fresh
        bus.inject(2,'1|20|old queued offer')
        bus.inject(2,'1|30|C3H1|303|host|0.0.35')
        b:update(0)
        fresh=b:extensionContext()
        assert(fresh and fresh.remoteSession=='303' and fresh.generation~=bc.generation)
        assert(b:takeExtension(fresh)==nil and not b:sendExtension(bc,20,'wrong session'))
        bus.inject(2,'0|0|peer_leave 1 quit'); b:update(0)
        assert(b:extensionContext()==nil and b:takeExtension(fresh)==nil)
        assert(not b:sendExtension(fresh,20,'departed peer'))
    """)


def test_extension_overflow_fails_only_experiment():
    runtime().execute(r"""
        pair(); b:enableExtensions(true)
        local context=b:extensionContext()
        for i=1,Net.MAX_EXTENSIONS+1 do bus.inject(2,'1|20|NT1|999|B|1|0|0|0|0') end
        b:update(0)
        assert(b:extensionContext()==nil and b:takeExtension(context)==nil)
        assert(b:status().extensionFault=='extension inbox overflow')
        assert(b:isV2() and b:status().ready and b:sample())
        assert(bus.clients[2].disconnects==0 and #b.extensionInbox==0)
        a:update(1/30); a:push(state(77)); b:update(1/30)
        assert(b:takePacket().x==77) -- player path remains operational
        b:enableExtensions(true)
        assert(b:extensionContext() and b:status().extensionFault==nil)
        assert(not b:sendExtension(context,20,'stale closure after overflow'))
    """)


def test_extension_not_available_before_application_readiness():
    runtime().execute(r"""
        local n=bus.native(1,'host')
        local net=Net.new({mode='v2',natives=n,probeDisabled=true,extensions=true})
        assert(net:extensionContext()==nil)
        net:update(0)
        bus.inject(1,'0|0|peer_join 2 joiner')
        bus.inject(1,'2|20|unsolicited extension')
        net:update(0)
        assert(net:extensionContext()==nil and #net.extensionInbox==0)
        bus.inject(1,'2|30|C3H1|202|joiner|0.0.35')
        net:update(0)
        assert(net:extensionContext()==nil) -- clock/player push still unavailable
        net:push(state())
        assert(net:extensionContext())
        local context=net:extensionContext()
        net:stop()
        assert(net:extensionContext()==nil and not net:sendExtension(context,20,'after shutdown'))
    """)


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
