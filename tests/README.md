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

- `-Only test_bot.py,test_mods.py` runs just those Python tests
- `-KeepWorkDir` keeps the per-run folder with each test's full log (it is always
  kept when something fails, and the path is printed)

Requirements:

- Python 3 on `PATH` (tested with 3.13), or set `COOP_PYTHON` to its path. The first
  run installs `requirements.txt` into `tests\.deps` with `pip --target`. That
  folder is gitignored; delete it to reinstall.
- `git` on `PATH` and the repository history: `test_join.py` reads the old join loop with
  `git show 30077d1:<init.lua>` to show it failing in the same mock game.
- For the redscript group, a game folder with redscript and Codeware. It defaults to
  `G:\SteamLibrary\steamapps\common\Cyberpunk 2077 - Test B`. Set `COOP_GAME_DIR`
  to use another one. `make_sandbox.py` reads only `engine\tools`,
  `r6\cache\final.redscripts` and `red4ext\plugins\Codeware\Scripts` from it, copies
  them into `%TEMP%\cp2077coop_scc_sandbox`, mirrors this repo's `.reds` files in,
  and `coop-tools\scc_check.py` compiles there without error popups. Never run
  `scc.exe` directly.

Each Python test runs in its own new folder under `%TEMP%`, because the mod's
`role.txt`, `coop_stats_*.txt` and `coop_events.log` are relative to the working
directory. `test_two_players.py` (which most tests import) moves into a private temp
folder on its own when it is run without the runner.

## Files

| File | Checks |
|---|---|
| `run_all.ps1` | Runs every group below: LuaJIT load (catches the 60-upvalue limit), redscript compile, the tests, the movement sim, the relay and the `coop-tools` syntax check |
| `make_sandbox.py` | Builds and refreshes the redscript compile sandbox |
| `test_state_sync.py` | State payload round trip through float32 and `%.6f`. Before the partner's first ping the host sends only flags; after it, world state, which the joiner applies |
| `test_two_players.py` | Two instances through a simulated LA-Warsaw-Russia relay: RTT, rates, join, role conflict, join give-up, STALE/LOST, panel buttons. Shared harness for most tests |
| `test_bot.py` | Test-pattern bot: the joiner sees every phase (walk, run, sprint, crouch, weapons, vehicle), car pose and drift bound |
| `test_combat.py` | A hit packet does not move the avatar or reset its state |
| `test_mods.py` | Mod list comparison is exact with packet loss |
| `test_live_bugs.py` | Regressions from the live test: join teleport, fast follow, remote vehicle |
| `test_join.py` | The joiner's teleport to the host against a mock game that drops teleports for the first 8 s after a load and applies them 0.6 s late: waits for 4 s of settled play (vehicle, scene and position jumps restart it), one Teleport per attempt, success measured at the teleport point, 3 logged attempts with growing pauses and a clean give-up, a late teleport still counts, "Teleport to host" takes the same path. Also runs the join loop from commit `30077d1` (read with `git show`) in the same game and checks that it gives up without moving the player |
| `test_timing.py` | Packet timing and ordering at different frame rates, stale and torn packets, no globals, upvalue limit |
| `test_avatar.py` | How the avatar walks, turns, catches up and settles |
| `test_robustness.py` | Missing redscript, failed spawns, stale time and weather |
| `test_diagnostics.py` | Stats and event files, `coop_monitor.py`, how the panel grades the connection, old-partner handling (role, version, constant vector length), the panel's monitor path and stale `monitor_status.txt`, the history CSV locked by Excel, the Codeware/DLL load check, `server.ini` encodings, ping output in any Windows language. The monitor runs write their history to a temp file (`--history`) |
| `test_relay.py` | `coop-tools\coop_relay.py`: forwarding, WELCOME, latency, loss |
| `coop_sim30.py` | Movement sim at 30 Hz: idle, moving and vehicle error, teleports, move commands (prints numbers, no pass/fail) |
| `coop_sim.py` | The same sim at 20 Hz, kept for comparison. Not run by `run_all.ps1` |

## Known failures

A check whose name contains `[KNOWN: ...]` is a tracked, known problem. If a test
fails only on such checks, and does not crash, the runner reports it as known
and does not count it. The `test_bot.py` drift item is the only tagged check.

The mock AI model is an assumption: walk 2.0, run 4.5 and sprint 7.0 m/s, a 0.1 s
command delay, and `CancelCommand` stops the NPC. The real values have to be
measured in the game.
