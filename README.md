# CP2077 Coop

Experimental two-player co-op for Cyberpunk 2077 (game version 2.31a).

The source tree now includes the native plugin (`plugin/`), relay (`relay/`), NPC
harness and measurement tools with their Git histories preserved. Start with
[shared development](docs/DEVELOPMENT.md), [integration provenance](docs/INTEGRATION_PROVENANCE.md)
and the [multiplayer roadmap](docs/MULTIPLAYER_PLAN.md). The deployed gameplay
baseline is still v0.0.37/alpha.5; build infrastructure and the opt-in headless
authority experiment do not constitute a new complete multiplayer release.

The [authority experiment](relay/docs/AUTHORITY_EXPERIMENT.md) adds host-approved
vehicle/seat metadata, baseline readiness and stale-session rejection over the
existing CB77 protocol. It is disabled by default and verified with actual UDP
clients under loss and reconnect. Connecting it to observed game mounting and
driving remains a later roadmap stage.

Each player sends their position, facing and gameplay state to a relay server over UDP.
The other player is shown as a stand-in NPC that copies that movement and state.

## Requirements

All of these ship inside the release zip:

- [Cyber Engine Tweaks](https://www.nexusmods.com/cyberpunk2077/mods/107) (Lua bridge)
- [RED4ext](https://www.nexusmods.com/cyberpunk2077/mods/2380) (loads the network plugin)
- [redscript](https://www.nexusmods.com/cyberpunk2077/mods/1511) (script compiler)
- [Codeware](https://www.nexusmods.com/cyberpunk2077/mods/7780) 1.18.0 (entity spawning, hiding the avatar while the other player drives)

## Install

1. Extract the release zip into the game folder (the one containing `bin`, `r6`, `red4ext`).
2. Edit `red4ext/plugins/CP2077Coop/server.ini` if the relay server address changed. Save it as
   UTF-8 **without** BOM (Notepad: *UTF-8*, not *UTF-8 with BOM* or *Unicode*), or let the dev kit
   write it: `python coop-tools/devkit.py server <game folder> IP:PORT`. The monitor warns about a
   BOM and reports a UTF-16 file or a missing `server_ip` / `server_port`.
3. Pick a role in the in-game **CP2077 Coop** panel (open the CET overlay, click *Switch to HOST/JOINER*).
   The choice is saved to `role.txt` in the mod folder. One player must be host, the other joiner.

**Both players must run the same version**: the panel title (*CP2077 Coop v0.0.37*), the
*Version* row at the top of the panel and `version=` at the start of every `[STATS]` line show it.
The protocol changes between builds: a partner on an older build shows up as *Peer: no ping reply -
other player on old version?* and *Role check: unknown*, not as a role problem. Update the older
side instead of switching roles. Against a v0.0.26 or v0.0.27 partner a current build still shows
position and facing, but the RTT and the role check stay empty.

## Run

Start the game normally. The **CP2077 Coop** panel shows connection state, player count,
player-to-player round trip, packet rates, missed packets, avatar drift, both players' state,
the joiner's teleport to the host and the relay server. Toggle it under CET *Bindings* →
*Toggle coop panel*.

**Partner marker.** A moving waypoint-style pin on the map/minimap follows the partner's
latest received world position, including while driving and beyond avatar spawn range.
The panel shows X/Y/Z coordinates, distance and the existing *Player RTT* ping.
The pin has its own ID and does not change your selected waypoint or GPS route.
Coordinates are the last received sample, not a claim of zero network delay. The marker
hides after 1.5 s without a player-position packet and clears on session reset, save
unload or CET shutdown. Combat hit positions never move the marker. Deploy `marker.reds`
with `init.lua`; an older script install keeps movement working and reports the marker
as unavailable. Icon visibility with the game's map filters requires a live check.

**Joining.** The joiner is teleported next to the host once their game has settled: 4 s in the
world in a row, not in a car and not in a scene that holds the player (a car, a scene or the game
moving the player after a load restarts the 4 s). Each attempt sends one teleport, waits up to
5 s for the game to move the player and checks the result against the point it sent the player
to. There are up to 3 attempts, 2 s and then 4 s apart. The panel's *Join* row shows each step.
If it gives up, *Teleport to host* runs the same steps again. The host's avatar appears after the
join, or straight away while the joiner sits in a car or a scene. Every attempt is logged as a
`WORLD SYNC` line with the measured error and how far the player moved.

### Opt-in native transport (v0.0.34)

Missing `transport.ini` keeps the established **v1** gameplay path. The **v2** path is
an experimental integration requiring the separately installed CP2077CoopNet native
plugin (alpha.5 or later with `Net_ConnectV2`, `Net_PushPlayer`, `Net_SampleRemote`) and
its v2 relay. Local two-instance testing on 2026-10-04 exercised v2 gameplay with
simulated delay/loss. It is not a two-PC internet validation or complete-world pass.

Before enabling it, close both games and disable the standalone `CoopNetCheck`
probe in each installation. The current probe supports an empty `disabled.txt`
in its mod folder and prints `disabled.txt present: gameplay owns Net_Poll` on
startup. Older probes without that gate must instead have
`bin/x64/plugins/cyber_engine_tweaks/mods/CoopNetCheck/init.lua` renamed to
`init.lua.disabled`. Disable any other `NetProbe` integration too: exactly one Lua
owner may connect, drain `Net_Poll`, and disconnect. Set the flag below only after
the probe is actually disabled; it does not disable another mod automatically.

Create `bin/x64/plugins/cyber_engine_tweaks/mods/CP2077Coop/transport.ini` in **each**
game installation (same room/key/relay, opposite roles in `role.txt`):

```ini
mode=v2
host=127.0.0.1
port=11778
room=codex-bench
key=
probe_disabled=true
npc_test=false
native_retarget=false
```

Restart the games after changing this file. `mode=auto` allows fallback to v1
before a v2 session starts if the relay/native API is unavailable or no compatible
peer appears. A rejected key/role or loss after a v2 session starts stays in v2;
it never silently reconnects through v1. Removing the file or choosing `mode=v1`
restores the legacy path. The dev kit deploys `net_transport.lua` with `init.lua`
and all `.reds`; it preserves each installation's `transport.ini` and excludes it
from new clones. Native plugin installation is separate from this dev kit.

Native player snapshots supply the interpolation buffer, velocity and sampled pose.
Rendering uses that pose directly, without the v1 prediction pass. The current native
API lacks a raw snapshot sequence/timestamp, so a separate `C3M1` movement envelope
on unreliable channel 1 supplies real packet counts, newest-position markers and
join targets. Repeated render queries are never counted as received packets. This
transitional format sends positions twice; it is not a completed compact protocol.
Legacy time/weather, ping and mod-list extras use reliable channel 16; channel 30
negotiates the application session. The panel identifies the active transport and
native sample mode/buffer delay.

**Current limits:** cars are cosmetic pose/model stand-ins, with no shared driving
physics, damage, passengers or authority. V2 suppresses legacy hit capture and does
not replicate combat/NPC authority, health/death, inventory, quests or player look
pitch (health and pitch fields are placeholders). The avatar remains a local NPC
following the sampled target, so pathfinding and animation still affect the visible
result. Default v1 retains its existing proximity-based ranged-hit approximation.
Neither transport constitutes full-world multiplayer synchronization yet.

### Optional controlled test NPC (v0.0.37)

Set `npc_test=true` in both installations' `transport.ini` to opt into one temporary
test actor. It requires v2 and the packaged `npc_test.lua`, `testnpc.lua` and
`testnpc.reds`; default false does not call NPC methods or spawn actors. This is a
controlled experiment. A 600.07-second interactive local test observed movement,
removal, pause, quickload and peer-exit cleanup; broad animation, collision,
streaming and repeated lifecycle coverage remain incomplete. It is separate from
the broader NPC prototype and never enables
population suppression, prevention changes, vanilla/quest binding or damage hooks.

After both sides show **NPC test: ready**, use the host's **Spawn test NPC** button.
The fixed generic NPC appears six metres west of the host. **Toggle NPC path** moves
the target north/south along a 12 m lane at 1.5 m/s; inspect clearance first. It is
a transform demonstration, not autonomous synchronized AI. The controlled NPC uses
`AITeleportCommand` after a single live command moved the actor while the old
`TeleportationFacility` path did not. Submissions are capped at 10 Hz, settled
poses/yaws enqueue nothing, and only one command may be pending. After two seconds
a pending command receives one cancellation request; replacement waits for a
terminal state. Scoped removal/session cleanup cancels any retained command.
The v0.0.37 path moved both actors in that local test. **Remove test NPC**
retries removal until acknowledged. The panel reads the actor's actual position;
`[NPC TEST]` events report state, message/expiry counts and movement-request/timeout
counters every five seconds. Accepted commands are not measured movement; the
host continues sending only its actual actor pose.
Primary actions are now at the top of the panel before diagnostics, so changing
diagnostic rows cannot shift a teleport button into the role switch.

One central adapter owns polling. Reliable channel 20 carries NPC opt-in and
lifecycle, and unreliable channel 2 carries measured actor poses at 10 Hz. Both
current application sessions, the authenticated sender, a fresh joiner activation
challenge and a fresh host NPC epoch must match before actor APIs can run. Reliable
creation/removal acknowledgement concerns actual actor attachment/removal, beyond
accepting a network send. A tagged handle is not enough: the actor must be attached
and measured within 2 m of its original requested spawn pose before movement or
creation ACK. A registered origin ghost expires after five seconds even while
network snapshots arrive, and logs an explicit placement failure. Old IDs/epochs
cannot revive removed actors. Request, measured placement and first transmitted
bind each log their role/epoch/actor ID/pose once, so an origin request is traceable.
Removal tracks the private created EntityID, population spawning and retiring
actor attachment after Codeware drops its tag. Cancellation during asynchronous
creation waits for the entity before deleting it. A five-second cleanup wait fails
the new spawn but keeps tracking the old actor; it cannot send a false removal ACK.
The adapter's
bounded extension inbox fails closed without disconnecting the player stream.

Host/joiner menu pause removes the experimental actor and renegotiates on resume;
respawn explicitly. Silence expires the peer actor after three seconds. Disconnect,
role change, save unload and CET shutdown request scoped actor cleanup. Codeware's session
cleanup is also registered before spawn. Appearance variation, AI, health/deaths,
combat, traffic and quest/world identity are outside this slice. The generic record
is fixed, but its runtime appearance is not serialized. No shared-world completion
claim follows from this experiment.

`native_retarget=true` separately enables the experimental retained AIMoveTo path
for the player stand-in. It is also false by default. Compare `commands_started`
and `retargets` in `[STATS]` plus actual visible drift before selecting it for normal
play; changing a target can behave differently in the real game than in the mock.

The tagged spawn/lifecycle interfaces follow [Codeware's primary documentation](https://github.com/psiberx/cp2077-codeware/wiki/)
(checked 2026-10-04). [Codeware 1.18.0 source](https://github.com/psiberx/cp2077-codeware/blob/b1b2770cdf6ad2631666fb6ef4ccda99d864298e/src/App/World/DynamicEntitySystem.cpp)
shows why tag removal is earlier than engine deletion and why pending creation
must retain its ID. Live removal still requires checking actual actor absence.
The broad NPC design and research remain in the Obsidian vault.

The standard test bot's sprint lasts **3 seconds**. The offline A10 regression uses
12 seconds and still has its documented measured-lag failure; a normal bot cycle
does not establish the 12-second live sprint result. The Oil Fields west-lane start
was inspected in-game, but the whole bot circle needs a clearance check before
interpreting corrections as transport error.

**What each v1 packet carries.** Each of the 30 packets per second carries one extra 9-bit value
besides position and facing. Player flags (crouch, weapon, aim, fire, in a vehicle, role) go in
every packet that has nothing else due, and never more than one slot late. In a steady session
that is 86% of the host's packets and 93% of the joiner's. The rest:

- ping and the reply to the partner's ping: once a second each
- time of day and weather (host only): once a second, and within about 0.1 s of a change
- the car's model index while driving: once a second, starting right after the first packet
  that says "in a vehicle"
- the installed-mods list: in a burst until both lists are compared plus two more full rounds,
  then one mod every 10 s. A partner that reloads or restarts gets the list again.

With a long mod list the burst costs flags for a while at the start: 30 mods take about 20 s.
Below 30 fps the game sends fewer packets per second, so the once-a-second values take a larger
share.

**Player RTT** is the network round trip between the two games plus up to a few frames of
waiting for the next send slot. Expect about **30-80 ms** when both players and the relay are in
the US, and about 250-400 ms on the Los Angeles - Warsaw relay - Russia route. The panel and the
monitor use the same limits (OK below 450 ms, WARN up to 700 ms, BAD above), set so the long route
reads OK; on a US-only route anything above ~150 ms is worth a look.

The game reads the network plugin once per frame and the plugin keeps only the newest packet,
so packet counts are split by cause:

- **Missed**: packets that never arrived in the last 5 s: network loss, or the partner's plugin
  sending several 30 Hz ticks of one long frame as a single packet (their side shows that as
  *merged*). Two packets bunched into one of your frames by jitter also land here.
- **Overwritten**: packets that arrived but were replaced before your game read them because
  your frame rate is below the partner's 30 packets/s. This is local, not the network.

With the local test relay (`coop-tools/coop_relay.py`), the monitor also shows the relay's own
count of what each game skipped and what the simulated link dropped.

### Live monitor

```bash
python coop-tools/coop_monitor.py
```

Checks compile and crash logs, plugin loading (Codeware 1.18.0 and the coop DLL, by RED4ext's
own "has been loaded" line), your ping to the relay and the in-game stats against expected ranges
every 5 s, and appends to `coop-tools/coop_monitor_history.csv` (`--history PATH` for another file).
The CSV uses this PC's Windows list separator and decimal mark (`;` and `,` on Russian or Polish
Windows), so a double-click opens it in columns in this PC's Excel; to open a file from a PC with
another language, use Data > From Text/CSV and pick the separator. It can stay open in Excel:
samples taken while Excel locks it are skipped with a WARN. When the columns or the separator
change, the old file is kept as `coop_monitor_history-until-<time>.csv`.
The ping works with any Windows language.

It also feeds the relay address and ping into the in-game panel through `monitor_status.txt`. If
the monitor dies or its window is closed, the panel says *monitor stopped N s ago* after 30 s and
no longer shows the old ping; after Ctrl+C it asks for the monitor again within 2 s. Requires Python 3.

Logs, all in `bin/x64/plugins/cyber_engine_tweaks/mods/CP2077Coop/` unless noted:

- `coop_stats_host.txt` / `coop_stats_joiner.txt`: the latest `[STATS]` line, rewritten every 5 s.
  Besides the connection numbers it has `frame_p99_ms` (99th percentile frame time of the last
  5 s), `hard_per_min` (avatar teleports that fix drift over the last minute; teleports that
  follow a dash or a car and the snap after spawning do not count) and `flags_rx_ps` (the
  partner's flags packets read per second). The monitor shows them as *sync detail* and keeps
  them, with the version, in the history CSV
- `coop_events.log`: every `[CP2077Coop]` line (events, world sync, errors) with the time; restarts
  with each game launch and keeps at most the last 400 lines
- `CP2077Coop.log`: Lua runtime errors only (written by CET)
- `bin/x64/plugins/cyber_engine_tweaks/scripting.log`: CET console output, including the mod's lines;
  CET buffers it, so recent lines can be missing until the game exits
- `r6/logs/redscript_rCURRENT.log`: script compile errors

## Tests

The offline suite needs no running game. Run it before every commit:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tests\run_all.ps1
```

See [tests/README.md](tests/README.md) for requirements and what each test covers.

## Source layout

| Path | Purpose |
|---|---|
| `bin/x64/plugins/cyber_engine_tweaks/mods/CP2077Coop/init.lua` | Network loop, avatar movement, state sync |
| `r6/scripts/CP2077Coop/natives.reds` | Declarations of functions exported by `CP2077Coop.dll` |
| `r6/scripts/CP2077Coop/remote.reds` | Spawning and teleporting the remote avatar |
| `r6/scripts/CP2077Coop/marker.reds` | Partner map/minimap marker, position updates and detach cleanup |
| `r6/scripts/CP2077Coop/state.reds` | Reading and applying crouch, weapon, time and weather |
| `r6/scripts/CP2077Coop/vehicle.reds` | The other player's car (placed every frame where they are now), mounted vehicle pose, avatar hidden while they drive |
| `red4ext/plugins/CP2077Coop/` | Network plugin (binary, source kept separately by Jakub) |
| `tests/` | Offline test suite: LuaJIT harness, simulations, redscript compile sandbox |
