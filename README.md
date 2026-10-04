# CP2077 Coop

Experimental two-player co-op for Cyberpunk 2077 (game version 2.31a).

Each player sends their position, facing and gameplay state to a relay server over UDP.
The other player is shown as a stand-in NPC that copies that movement and state.

## Requirements

All of these ship inside the release zip:

- [Cyber Engine Tweaks](https://www.nexusmods.com/cyberpunk2077/mods/107) (Lua bridge)
- [RED4ext](https://www.nexusmods.com/cyberpunk2077/mods/2380) (loads the network plugin)
- [redscript](https://www.nexusmods.com/cyberpunk2077/mods/1511) (script compiler)
- [Codeware](https://www.nexusmods.com/cyberpunk2077/mods/7780) 1.18.0 (entity spawning)

## Install

1. Extract the release zip into the game folder (the one containing `bin`, `r6`, `red4ext`).
2. Edit `red4ext/plugins/CP2077Coop/server.ini` if the relay server address changed.
3. Set the role in `bin/x64/plugins/cyber_engine_tweaks/mods/CP2077Coop/init.lua`:
   - host: `local IS_HOST = true`
   - joiner: `local IS_HOST = false`

Both players must run the same version.

## Run

Start the game normally. The CET console (default key `~`) prints `[CP2077Coop]` status lines.
Logs: `bin/x64/plugins/cyber_engine_tweaks/mods/CP2077Coop/CP2077Coop.log` and `r6/logs/redscript_rCURRENT.log`.

## Source layout

| Path | Purpose |
|---|---|
| `bin/x64/plugins/cyber_engine_tweaks/mods/CP2077Coop/init.lua` | Network loop, avatar movement, state sync |
| `r6/scripts/CP2077Coop/natives.reds` | Declarations of functions exported by `CP2077Coop.dll` |
| `r6/scripts/CP2077Coop/remote.reds` | Spawning and teleporting the remote avatar |
| `r6/scripts/CP2077Coop/state.reds` | Reading and applying crouch, weapon, time and weather |
| `red4ext/plugins/CP2077Coop/` | Network plugin (binary, source kept separately by Jakub) |
