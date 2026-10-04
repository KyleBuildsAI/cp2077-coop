# CP2077 Coop - NPC / world sync prototype

Host-authoritative NPC, crowd and traffic sync for the CP2077 Coop mod (Cyberpunk 2077 2.31a).
The host streams the NPCs and vehicles around both players. The joiner hides its own random
population. It then shows the host's NPCs, either by binding its own copy of each placed NPC
(same static EntityID in both games) or by spawning a proxy from the NPC's record and appearance.

This is a prototype. It compiles against the 2.31 scripts, Codeware 1.18 and the current
CP2077Coop scripts, but it has not been run in game yet.

## Requirements

- Cyberpunk 2077 2.31a with redscript, RED4ext, Codeware 1.18 and CET (as for CP2077Coop)
- A transport that carries string payloads, such as `Net_Send` / `Net_Poll` from the
  CP2077CoopNet plugin in `../dllproto`
- Python 3.11+ and pytest, for the reference codec tests (`pip install -r requirements.txt`)

## Layout

| Path | Purpose |
|---|---|
| `r6/scripts/CP2077Coop_npcsync/npc_common.reds` | State struct, enums, quantization and the text wire format (the message format is documented at the top of the file) |
| `r6/scripts/CP2077Coop_npcsync/npc_host.reds` | Host: finds NPCs around both players, captures their state, manages the netId table, and handles bind, death and unbind messages and hits from the joiner |
| `r6/scripts/CP2077Coop_npcsync/npc_joiner.reds` | Joiner: hides its own population, binds mirrors or spawns proxies, applies snapshots, deaths and vehicles |
| `r6/scripts/CP2077Coop_npcsync/npc_system.reds` | ScriptableSystem: role, outboxes, Codeware entity registry, `DealDamages` damage routing, PlayerPuppet API for Lua |
| `r6/scripts/CP2077Coop_npcsync/npc_probe.reds` | Identity probe that dumps NPC ids, records and appearances for comparison between the two games |
| `tools/npc_codec.py` | Python twin of the text codec (same quantization and chunking) |
| `tools/npc_probe_diff.py` | Compares host and joiner probe dumps (mirror match rate, appearance and record mismatches) |
| `tools/compile_check.sh` | Compile-checks the scripts with the popup-free `scc_check.py` |
| `tests/test_npc_codec.py` | Codec round trip, size limits, bandwidth simulation and probe diff tests |

## Run the checks

```bash
bash tools/compile_check.sh          # redscript compile check (needs ../../sccgame)
python -m pytest tests -q            # codec, size, bandwidth and probe-diff tests
```

## Install (once the transport plugin is in place)

1. Copy `r6/scripts/CP2077Coop_npcsync` into the game's `r6/scripts`.
2. From `init.lua`, set the role with `player:CP2077Coop_NpcSetRole(1)` (host) or `(2)` (joiner).
3. Host, 10 times a second: call `player:CP2077Coop_NpcHostTick()`. Then send the strings returned
   by `CP2077Coop_NpcTakeReliable()` on a reliable channel and those from
   `CP2077Coop_NpcTakeUnreliable()` on an unreliable channel.
4. Joiner, every frame: call `player:CP2077Coop_NpcJoinerUpdate()`, then send whatever
   `CP2077Coop_NpcTakeReliable()` returns (hit reports).
5. On receive: for `NB1` messages, call
   `player:CP2077Coop_NpcReceiveBind(msg, TweakDBID.new(recHash, recLen))`.
   `recHash` and `recLen` are fields 6 and 7 of the message. Pass every other message to
   `player:CP2077Coop_NpcReceive(msg)`.

## Test bench: check the identity assumption first

Load the same save on both games and stand at the same spot. On each game, write the lines from
`Game.GetPlayer():CP2077Coop_NpcProbe(80.0)` to the log. Then compare the two logs:

```bash
python tools/npc_probe_diff.py host.log joiner.log
```

A high `static_match_pct` means most placed NPCs can be mirrored. Everything else needs a proxy.
