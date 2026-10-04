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
2. Edit `red4ext/plugins/CP2077Coop/server.ini` if the relay server address changed.
3. Pick a role in the in-game **CP2077 Coop** panel (open the CET overlay, click *Switch to HOST/JOINER*).
   The choice is saved to `role.txt` in the mod folder. One player must be host, the other joiner.

Both players must run the same version.

## Run

Start the game normally. The **CP2077 Coop** panel shows connection state, player count,
player-to-player round trip, packet rates, missed packets, avatar drift, both players' state
and the relay server. Toggle it under CET *Bindings* → *Toggle coop panel*.

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

Checks compile and crash logs, plugin loading, your ping to the relay and the in-game stats
against expected ranges every 5 s, and appends to `coop-tools/coop_monitor_history.csv`.
It also feeds the relay address and ping into the in-game panel. Requires Python 3.

Logs, all in `bin/x64/plugins/cyber_engine_tweaks/mods/CP2077Coop/` unless noted:

- `coop_stats_host.txt` / `coop_stats_joiner.txt`: the latest `[STATS]` line, rewritten every 5 s
- `coop_events.log`: every `[CP2077Coop]` line (events, world sync, errors) with the time; restarts
  with each game launch and keeps at most the last 400 lines
- `CP2077Coop.log`: Lua runtime errors only (written by CET)
- `bin/x64/plugins/cyber_engine_tweaks/scripting.log`: CET console output, including the mod's lines;
  CET buffers it, so recent lines can be missing until the game exits
- `r6/logs/redscript_rCURRENT.log`: script compile errors

## Source layout

| Path | Purpose |
|---|---|
| `bin/x64/plugins/cyber_engine_tweaks/mods/CP2077Coop/init.lua` | Network loop, avatar movement, state sync |
| `r6/scripts/CP2077Coop/natives.reds` | Declarations of functions exported by `CP2077Coop.dll` |
| `r6/scripts/CP2077Coop/remote.reds` | Spawning and teleporting the remote avatar |
| `r6/scripts/CP2077Coop/state.reds` | Reading and applying crouch, weapon, time and weather |
| `r6/scripts/CP2077Coop/vehicle.reds` | The other player's car (placed every frame where they are now), mounted vehicle pose, avatar hidden while they drive |
| `red4ext/plugins/CP2077Coop/` | Network plugin (binary, source kept separately by Jakub) |
