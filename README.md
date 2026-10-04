# CP2077 Coop

Experimental two-player co-op for Cyberpunk 2077 (game version 2.31a).

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

**Both players must run the same version**: the panel title (*CP2077 Coop v0.0.32*), the
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

**Joining.** The joiner is teleported next to the host once their game has settled: 4 s in the
world in a row, not in a car and not in a scene that holds the player (a car, a scene or the game
moving the player after a load restarts the 4 s). Each attempt sends one teleport, waits up to
2.5 s for the game to move the player and checks the result against the point it sent the player
to. There are up to 3 attempts, 2 s and then 4 s apart. The panel's *Join* row shows each step.
If it gives up, *Teleport to host* runs the same steps again. The host's avatar appears after the
join, or straight away while the joiner sits in a car or a scene. Every attempt is logged as a
`WORLD SYNC` line with the measured error and how far the player moved.

**What each packet carries.** Each of the 30 packets per second carries one extra 9-bit value
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
The CSV can stay open in Excel: samples taken while Excel locks it are skipped with a WARN. When
the columns change, the old file is kept as `coop_monitor_history-until-<time>.csv`.
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
| `r6/scripts/CP2077Coop/state.reds` | Reading and applying crouch, weapon, time and weather |
| `r6/scripts/CP2077Coop/vehicle.reds` | The other player's car (placed every frame where they are now), mounted vehicle pose, avatar hidden while they drive |
| `red4ext/plugins/CP2077Coop/` | Network plugin (binary, source kept separately by Jakub) |
| `tests/` | Offline test suite: LuaJIT harness, simulations, redscript compile sandbox |
