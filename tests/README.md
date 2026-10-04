# Offline test suite

Runs the real `init.lua` in LuaJIT 2.1 (through `lupa`) with mocked CET and game
APIs, compiles the redscript files in a throwaway sandbox, and exercises the relay
and the Python tools. No game is launched and nothing is written to a game folder.

## Run

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tests\run_all.ps1
```

The run must end with `TOTAL FAILED GROUPS: 0`. The exit code is the number of
failed groups. Options:

- `-Only test_bot.py,test_mods.py` runs just those tests; `test_relay.py` and
  `coop_sim30.py` run as the relay and movement sim groups; any other name fails with
  the list of valid names
- `COOP_TEST_TIMEOUT` (seconds, default 180) limits every Python run; a run over it has
  its process tree killed, prints `FAIL  timed out ...` and counts as a failed group
- `-KeepWorkDir` keeps the per-run folder with each test's full log (it is always
  kept when something fails, and the path is printed)

Requirements:

- Python 3 on `PATH` (tested with 3.13), or set `COOP_PYTHON` to its path. The first
  run installs `requirements.txt` into `tests\.deps` with `pip --target`. That
  folder is gitignored; delete it to reinstall.
- For the redscript group, a game folder with redscript and Codeware. It defaults to
  `G:\SteamLibrary\steamapps\common\Cyberpunk 2077 - Test B`. Set `COOP_GAME_DIR`
  to use another one. `make_sandbox.py` reads only `engine\tools`,
  `r6\cache\final.redscripts` and every `red4ext\plugins\*\Scripts` folder except
  CP2077Coop's own (the set the game compiles, e.g. Codeware and CP2077CoopNet) from
  it, copies them into a sandbox, mirrors this repo's `.reds` files in, and
  `coop-tools\scc_check.py` compiles there without error popups. The runner builds a
  fresh sandbox inside its per-run folder (so concurrent runs never compile each
  other's scripts), keeps the compiler's `redscript_rCURRENT.log` next to the test
  logs and deletes the sandbox afterwards (about 20 MB). Run by hand,
  `make_sandbox.py` defaults to `%TEMP%\cp2077coop_scc_sandbox`. Never run `scc.exe`
  directly.

Each Python test runs in its own new folder under `%TEMP%`, because the mod's
`role.txt`, `coop_stats_*.txt` and `coop_events.log` are relative to the working
directory. `test_two_players.py` (which most tests import) moves into a private temp
folder on its own when it is run without the runner.

## Files

| File | Checks |
|---|---|
| `run_all.ps1` | Runs every group below: LuaJIT load (catches the 60-upvalue limit), redscript compile, the tests, the movement sim, the relay and the `coop-tools` syntax check |
| `make_sandbox.py` | Builds and refreshes the redscript compile sandbox |
| `test_make_sandbox.py` | The sandbox gets every plugin's Scripts folder (subfolders too) but not CP2077Coop's own, drops removed plugins, refuses a folder inside the game or the repo |
| `run_with_timeout.py` | Runs one Python command with the runner's time limit and kills the whole process tree on timeout |
| `test_state_sync.py` | State payload round trip through float32 and `%.6f`. Before the partner's first ping the host sends only flags; after it, world state, which the joiner applies |
| `test_two_players.py` | Two instances through a simulated LA-Warsaw-Russia relay: RTT, rates, join, role conflict, join give-up, STALE/LOST, panel buttons. Shared harness for most tests |
| `test_bot.py` | Test-pattern bot: the joiner sees every phase (walk, run, sprint, crouch, weapons, vehicle), car pose and drift bound |
| `test_combat.py` | A hit packet damages the nearest live NPC within 4 m (skipping dead ones, non-NPCs and the avatar itself) through a 220 m targeting scan, queues the hit reaction, and does not move the avatar or reset its state |
| `test_mods.py` | Mod list comparison is exact with packet loss; a cycle whose last pair or end marker was lost still counts (and does not push a full cycle out of the comparison), a stale end marker after a reset does not |
| `test_live_bugs.py` | Regressions from the live test: join teleport, fast follow, remote vehicle |
| `test_join.py` | The joiner's teleport to the host against a mock game that drops teleports for the first 8 s after a load and applies them 0.6 s late: waits for 4 s of settled play (vehicle, scene and position jumps restart it), one Teleport per attempt, success measured at the teleport point, 3 logged attempts with growing pauses and a clean give-up, a late teleport still counts, "Teleport to host" takes the same path, the panel's *Join* row in every phase, and the host's own position must be steady too (a host still loading or fast travelling is not joined at its stale spot). Also runs the join loop from commit `30077d1` (kept as `tests/fixtures/init_30077d1.lua`, pinned by its git blob hash) in the same game and checks that it gives up without moving the player |
| `test_timing.py` | Packet timing and ordering at different frame rates, stale and torn packets, no globals (a runtime trap from load on, plus a static scan of every function's bytecode against a CET/Lua allow-list), upvalue limit |
| `test_avatar.py` | How the avatar walks, turns, catches up and settles |
| `test_robustness.py` | Missing redscript, failed spawns, stale time and weather |
| `test_diagnostics.py` | Stats and event files, `coop_monitor.py`, how the panel grades the connection, old-partner handling (role, version, constant vector length), the panel's monitor path and stale `monitor_status.txt`, the history CSV locked by Excel, the Codeware/DLL load check, `server.ini` encodings, ping output in any Windows language, the new `[STATS]` fields (frame p99 with hitches, hard corrections that leave out fast-follow teleports, partner flags per second against what really arrived, within 15% between the roles), the version in `[STATS]`, the panel title and the monitor's history, and that `devkit.py` deploy and make-instance skip rotated history files, the status tmp swap and logs, and that make-instance does not copy the test-bot switch. The monitor runs write their history to a temp file (`--history`) |
| `test_payload_schedule.py` | What each packet carries, per role, with two instances in the `test_timing` harness: over 10,000 slots each, player flags in at least 85% for host and joiner, time and weather at 1 Hz (and within 0.15 s of a change), ping at 1 Hz, every mod hash at least twice in the first 10 s, never more than 2 non-flag packets in a row. Long mod lists: the burst ends and flags stay at 85% or more after it. The vehicle index follows the first "in a vehicle" flags, goes again after 0.2 s and repeats at 0.5 Hz or more while flags stay at 85% or more in the car; a lost "in a vehicle" flags or index packet, or a 24/27 fps joiner, still shows the car within 0.3-0.5 s. A host at 20 fps keeps flags at 85% on foot and mounted (non-flag slots are budgeted at 14.5%), with ping, time, weather and index repeats slower but never more than 6 s apart. After a partner's reload, with or without a pause in its packets, the lists are sent again and both sides go back to one pair every 10 s. Mod comparisons are read from the panel (`Mods.compare`). Pointed at an older `init.lua` it shows the old split (host 23%, joiner 70% flags) |
| `test_relay.py` | `coop-tools\coop_relay.py`: forwarding, WELCOME, latency, loss |
| `coop_sim30.py` | Movement sim at 30 Hz: idle, moving and vehicle error, teleports, move commands. Fails when a seed leaves `BOUNDS` or the mod logs an ERROR / FAILED / NOT RESPONDING line |
| `coop_sim.py` | The same sim at 20 Hz, kept for comparison. Not run by `run_all.ps1` |

## Known failures

A check whose name contains `[KNOWN: ...]` is a tracked, known problem. If a test
fails only on such checks, and does not crash, the runner reports it as known
and does not count it. Remove a tag as soon as its check passes, or a later
regression in it is not counted; the runner prints a note when a tagged check passes.

The mock AI model is an assumption: walk 2.0, run 4.5 and sprint 7.0 m/s, a 0.1 s
command delay, and `CancelCommand` stops the NPC. The real values have to be
measured in the game.
