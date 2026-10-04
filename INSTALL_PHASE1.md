# CP2077CoopNet 0.1.1: Phase 1 install and in-game check

Phase 1 answers one question: can our own RED4ext plugin register natives that CET and redscript
can call, next to Jakub's `CP2077Coop.dll`? This guide installs the plugin into the two bench game
folders, starts the bench relay on port 11779, and lists the log lines and CET console output that
prove it works. Nothing here replaces or changes v1: Jakub's DLL, its natives and the v1 relay on
port 11778 keep working as before.

Bench folders used below (change them if yours differ):

```
G:\SteamLibrary\steamapps\common\Cyberpunk 2077 - Baseline
G:\SteamLibrary\steamapps\common\Cyberpunk 2077 - Test B
```

Requirements already present on the bench: game 2.31 (exe file version 3.0.80.51928), RED4ext 1.30,
CET, Codeware, and Jakub's coop mod. Python 3.11 or newer is needed for the relay.

## 1. What gets installed

```
dist\red4ext\plugins\CP2077CoopNet\CP2077CoopNet.dll
dist\red4ext\plugins\CP2077CoopNet\Scripts\Natives.reds
dist\red4ext\plugins\CP2077CoopNet\Scripts\Helpers.reds
```

(`dist` is `D:\Downloads\syncfix\coopnet\dllproto\dist`.) The plugin goes in its own folder. It
does not touch `red4ext\plugins\CP2077Coop\` (Jakub's DLL). On load it hands its own `Scripts`
folder to the redscript compiler, so **do not** also copy the `.reds` files into `r6\scripts`:
that would declare the natives twice and break the script compile. The only exception is the
fallback in section 7. The install in section 2 and the uninstall in section 8 remove that copy
again.

The build that produced this `dist` is listed under "Build provenance" at the end of this file.

## 2. Install (both games closed)

Run in PowerShell:

```powershell
# Must print nothing. If it lists a process, close the games first.
Get-Process Cyberpunk2077 -ErrorAction SilentlyContinue

$src   = 'D:\Downloads\syncfix\coopnet\dllproto\dist\red4ext\plugins\CP2077CoopNet'
$games = 'G:\SteamLibrary\steamapps\common\Cyberpunk 2077 - Baseline',
         'G:\SteamLibrary\steamapps\common\Cyberpunk 2077 - Test B'
foreach ($game in $games) {
    $dest = Join-Path $game 'red4ext\plugins\CP2077CoopNet'
    # A section 7 fallback copy would declare the natives a second time next to the plugin's own
    # Scripts folder, and the failed compile would take Jakub's CP2077Coop scripts down with it.
    $fallback = Join-Path $game 'r6\scripts\CP2077CoopNet'
    if (Test-Path $fallback) {
        Remove-Item -Recurse -Force $fallback
        "$game -> removed the r6\scripts\CP2077CoopNet fallback copy"
    }
    # /MIR makes the folder an exact copy (an older version's leftovers are removed).
    # Robocopy exit codes 0-7 mean success.
    robocopy $src $dest /MIR /NJH /NJS /NDL
    Get-ChildItem -Recurse -File $dest | Select-Object FullName, Length
    (Get-FileHash -Algorithm SHA256 (Join-Path $dest 'CP2077CoopNet.dll')).Hash
    "$game -> r6\scripts\CP2077CoopNet present: $(Test-Path $fallback)"
}
```

Each game must list exactly `CP2077CoopNet.dll`, `Scripts\Helpers.reds` and `Scripts\Natives.reds`,
and the DLL hash must match the one under "Build provenance". The last line for each game must say
`present: False`. If the new build's log again says `scripts NOT added`, apply the section 7
fallback again.

Then refresh the mod list that the coop panel compares, so both instances list
`red4ext/CP2077CoopNet` and the v1 "mods compared" step still matches:

```powershell
python 'G:\SteamLibrary\steamapps\common\Cyberpunk 2077 - Baseline\coop-tools\devkit.py' modlist `
    'G:\SteamLibrary\steamapps\common\Cyberpunk 2077 - Baseline' `
    'G:\SteamLibrary\steamapps\common\Cyberpunk 2077 - Test B'
```

`devkit.py deploy` does not know about CP2077CoopNet yet. If you run it later it leaves this
folder alone, but it rewrites `modlist.txt`, which then still includes the plugin because the
folder is there.

Optional offline checks of the installed copy (games may stay closed):

```powershell
cd D:\Downloads\syncfix\coopnet\dllproto
python tools\verify_exports.py 'G:\SteamLibrary\steamapps\common\Cyberpunk 2077 - Baseline\red4ext\plugins\CP2077CoopNet\CP2077CoopNet.dll'
#   ... VERIFY PASS
build\Release\coopnet_plugin_probe.exe 'G:\SteamLibrary\steamapps\common\Cyberpunk 2077 - Baseline\red4ext\plugins\CP2077CoopNet\CP2077CoopNet.dll'
#   Query(): name=CP2077CoopNet ... version=0.1.1 sdk=1.0.0 runtime=3.0.80.51928 ... PROBE PASS
python 'D:\Downloads\syncfix\MP=Jakub\coop-tools\scc_check.py' 'G:\SteamLibrary\steamapps\common\Cyberpunk 2077 - Baseline'
#   OK: compiled r6\scripts, ... (the list must include red4ext\plugins\CP2077CoopNet\Scripts)
```

`scc_check.py` compiles `r6\scripts` plus every `red4ext\plugins\*\Scripts` folder with the game's
own compiler library. It shows no popups and writes only to `%TEMP%`. Never run `scc.exe` directly.

## 3. Start the bench relay on port 11779

The plugin speaks its own protocol (CPN2 frames, protocol 1). Jakub's `coop_relay.py` cannot carry
it, so the plugin gets its own relay on **11779**. The v1 relay stays on **11778**. They are separate
processes on separate ports and never collide.

In a second PowerShell window:

```powershell
cd D:\Downloads\syncfix\coopnet\dllproto
python tools\coopnet_relay.py --port 11779 --latency-ms 115 --jitter-ms 20 --loss-pct 1 | Tee-Object -FilePath relay_11779.log
```

Expected right away:

```
HH:MM:SS coopnet relay v1 on 127.0.0.1:11779 latency=115.0ms jitter=20.0ms loss=1.0% reorder=False
```

When the games connect (step 5), it prints one line per game:

```
HH:MM:SS EVENT client joined #1 127.0.0.1:<port> room=default nonce=<hex>
HH:MM:SS EVENT client joined #2 127.0.0.1:<port> room=default nonce=<hex>
```

It also prints a `[STATS]` block every 5 s. It listens on 127.0.0.1 only, which is enough when both
instances run on this PC. Two PCs need `--host 0.0.0.0` and UDP 11779 open in the firewall. Leave the
window open for the whole session; Ctrl+C stops it.

This exact command was rehearsed offline: two copies of the plugin's network code (`coopnet_loopback.exe`)
went through it, and 400 + 200 reliable messages arrived in order in 1.5 s.

## 4. Launch both games and read the logs

Start both instances as usual, through the main menu (loading a save is not needed yet).
Three logs prove that the plugin loaded. `<game>` is the game folder.

**a) RED4ext loader log**: the newest `<game>\red4ext\logs\red4ext-*.log`

```
... CP2077CoopNet (version: 0.1.1, author(s): CP2077 Coop) has been loaded
... Adding paths to redscript compilation:
... CP2077CoopNet: '<game>\red4ext\plugins\CP2077CoopNet\Scripts'
```

**b) The plugin's own log**: the newest `<game>\red4ext\logs\cp2077coopnet-<date>-<time>.log`

RED4ext 1.30 names the log after the DLL, lower case, plus a timestamp. Each line starts with
`[YYYY-MM-DD HH:MM:SS.mmm] [info    ] [<thread>] [CP2077CoopNet]`. Expected, in this order:

```
game file version 3.0.80.51928 (2.31) ok
added <game>\red4ext\plugins\CP2077CoopNet\Scripts to the redscript compilation
CP2077CoopNet 0.1.1 proto 1 loaded; the Net_* natives are added at RTTI post-register
CP2077CoopNet 0.1.1 proto 1: registered Net_* natives (10/10): Net_Connect, Net_ConnectRoom, Net_Disconnect, Net_Send, Net_SendTo, Net_Poll, Net_Stats, Net_LocalId, Net_NowMs, Net_Version; scripts added: <game>\red4ext\plugins\CP2077CoopNet\Scripts
```

The last line is **the** Phase 1 line. It is written once, at RTTI post-register. A native is
listed there only when three checks passed: every parameter type and the return type resolved in
RTTI, and looking the name up in RTTI after `RegisterFunction` returned the function the plugin
registered. A native that fails a check is not counted. It is listed after `MISSING:` together
with the step that failed, and the whole line is then logged at `[error]`. What the line cannot
show is that a call works; step 5 checks that from CET and redscript. The marker text
`registered Net_* natives` appears nowhere else in the DLL (the export check counts it). To grep
both games:

```powershell
foreach ($game in $games) {
    Select-String -Path (Join-Path $game 'red4ext\logs\cp2077coopnet-*.log') -SimpleMatch 'registered Net_* natives (10/10)' |
        Select-Object -Last 1 | ForEach-Object { "$game -> $($_.Line)" }
}
```

**c) Redscript log**: `<game>\r6\logs\redscript_rCURRENT.log`

It lists `...\red4ext\plugins\CP2077CoopNet\Scripts\Helpers.reds` and `...\Natives.reds` among the
compiled files, and ends with `Compilation complete` and `Output successfully saved`. There is no
redscript error popup.

## 5. CET console commands (in each game)

Open the CET overlay and type one line at a time into its console. The expected output follows each command.

**Step 1: natives resolve (no relay needed)**

```lua
print(Game.Net_Version())
```
`CP2077CoopNet 0.1.1 proto 1`

```lua
print(string.format("%.3f", Game.Net_NowMs()))
print(type(Game.Net_NowMs()))
```
`1791...` (milliseconds since 1970-01-01 UTC, with a fraction) and `number`. Plain `print(Game.Net_NowMs())`
shows only 14 significant digits, for example `1791099704063.8` (one decimal place), so use the
`string.format("%.3f", ...)` line above to see the sub-millisecond part. `Net_NowMs` returns a
Double on purpose: CET would turn an Int64 into LuaJIT cdata (`123LL`), while a Double is a plain Lua
number. Both games read the same Windows clock, so their stamps compare directly.

```lua
print(Game.CoopNet_SelfTest())
```
`redscript ok: CP2077CoopNet 0.1.1 proto 1, Net_NowMs=<number>, clock ok`. This is a redscript
function from `Helpers.reds` that calls `Net_Version` and `Net_NowMs`, so it proves the natives also
resolve **from redscript**.

**Step 2: connect to the bench relay.** Run this in Baseline first, then in Test B:

```lua
print(Game.Net_Connect("127.0.0.1", 11779))
```
`true`

Drain the inbox:

```lua
for i = 1, 64 do local m = Game.Net_Poll() if m == "" then break end print(m) end
```
In Baseline:
```
0|0|connecting 127.0.0.1:11779
0|0|welcome 1
0|0|peer_join 2          (appears once Test B has connected; poll again)
```
In Test B, the same lines with `welcome 2` and `peer_join 1`.

```lua
print(Game.Net_Stats())
```
A JSON object with `"state":"connected"`, `"id":1` or `2`, `"version":"CP2077CoopNet 0.1.1 proto 1"`,
`"relay":"127.0.0.1:11779"` and a `"peers"` entry for the other game. That entry's `"rttMs"` should
settle around 230-270 ms (115 ± 20 ms each way). `"relayRttMs"` stays near 0, because the relay
answers pings without the simulated delay.

**Step 3: one reliable message each way**

In Baseline:
```lua
print(Game.Net_Send(16, "hello from " .. Game.Net_LocalId()))
```
`true`. It returns `false` if no peer has joined yet.

In Test B, run the drain line from step 2 again. Expected: `1|16|hello from 1`. Then send back from
Test B and drain in Baseline.

**Step 4: drain cost (exit criterion: under 0.1 ms per frame)**

```lua
local t = Game.Net_NowMs() for i = 1, 256 do if Game.Net_Poll() == "" then break end end print(string.format("drain %.4f ms", Game.Net_NowMs() - t))
```

**Step 5: disconnect**

```lua
Game.Net_Disconnect()
```
The other game's next drain shows `0|0|peer_leave <id> left`. The relay prints
`EVENT client left #<id> ... (bye)`.

The CET helper `lua\coopnet.lua` wraps the same calls (`CoopNet.version()`, `CoopNet.nowMs()`,
`CoopNet.pollTimed(handler)` and others) for when the probe module is written. It is not needed for
this check.

## 6. Success checklist

- [ ] Both `red4ext-*.log` files: `CP2077CoopNet (version: 0.1.1, ...) has been loaded` and the
      `CP2077CoopNet: '...\CP2077CoopNet\Scripts'` path line.
- [ ] Both `cp2077coopnet-*.log` files: `registered Net_* natives (10/10)` with `scripts added:`.
- [ ] Both `redscript_rCURRENT.log` files: the two CP2077CoopNet `.reds` files compiled, with no error.
- [ ] CET in both games: `Net_Version` returns the version string, `Net_NowMs` returns a `number`,
      and `CoopNet_SelfTest` returns `redscript ok ... clock ok`.
- [ ] `Net_Connect` returns true, `welcome` and `peer_join` arrive, `Net_Stats` shows `connected` with
      one peer, and a message gets through in each direction.
- [ ] The relay window shows `client joined #1` and `#2` in `room=default`.
- [ ] v1 still syncs as before over 11778; no crash on load, on loading a save, or on quit.

## 7. If something fails

| What you see | What it means | What to do |
|---|---|---|
| No `cp2077coopnet-*.log`, and the RED4ext log has `Could not load plugin 'CP2077CoopNet'` | Windows could not load the DLL | Check that the DLL is in `red4ext\plugins\CP2077CoopNet\`. Run the probe from step 2 on the installed copy. |
| RED4ext log: `CP2077CoopNet did not initialize properly, unloading...` | `Main` returned false | The plugin log says why, for example `unsupported game version ...`. |
| Plugin log has the `loaded` line but no `registered Net_* natives` line | The RTTI post-register callback never ran | Natives are absent. Report it; this is the Phase 1 hard-stop question. |
| Summary line at `[error]` with `MISSING: Net_X (<step>)` | That native failed a registration check: a parameter or return type is not in RTTI (`... not in RTTI, native not registered`), or the RTTI lookup after `RegisterFunction` did not return it | Report the summary line and the `Net_X not registered: ...` error line above it. The missing natives cannot be used. If CET can still call one of them (step 5), the lookup check is wrong, not the registration; report that too. |
| Summary line with `scripts NOT added: ...` | `sdk->scripts->Add` refused the folder | Fallback from the plan: copy `Scripts\*.reds` into `<game>\r6\scripts\CP2077CoopNet\` **and** delete the plugin's `Scripts` folder, so the natives are declared only once. From then on the r6 copy stands in for the plugin's folder and must go whenever the DLL goes or the plugin's `Scripts` folder comes back. Otherwise the game is left with declarations for natives that no DLL provides, or with every native declared twice, which fails the whole script compile including v1. The section 8 uninstall and the section 2 reinstall both remove it. |
| CET: `attempt to call a nil value (field 'Net_Version')` | CET does not see the native | Check the summary line first. |
| `Game.Net_*` works but `Game.CoopNet_SelfTest` is nil | The redscript side did not compile our files | Check `redscript_rCURRENT.log`. |
| `Net_Connect` returns true but no `welcome` arrives | The relay is not reachable | Check that the relay window is open on 11779. `Net_Stats` `lastError` gives details. |
| `0|0|rejected protocol_version` | Relay and plugin speak different protocol versions | Use `tools\coopnet_relay.py` from the same checkout. |

## 8. Uninstall

With the games closed, remove the plugin folder **and** the r6 fallback copy, if the section 7
fallback was ever applied. The fallback copy declares the `Net_*` natives. Without the DLL behind
them, the game would be left with declarations for natives that do not exist.

```powershell
# Must print nothing. If it lists a process, close the games first.
Get-Process Cyberpunk2077 -ErrorAction SilentlyContinue

$games = 'G:\SteamLibrary\steamapps\common\Cyberpunk 2077 - Baseline',
         'G:\SteamLibrary\steamapps\common\Cyberpunk 2077 - Test B'
foreach ($game in $games) {
    foreach ($folder in 'red4ext\plugins\CP2077CoopNet', 'r6\scripts\CP2077CoopNet') {
        $path = Join-Path $game $folder
        if (Test-Path $path) {
            Remove-Item -Recurse -Force $path
            "$game -> removed $folder"
        }
        "$game -> $folder present: $(Test-Path $path)"
    }
}
```

Both lines per game must end in `present: False`. Then run the `devkit.py modlist` command from
step 2 again. Once both folders are gone, Jakub's plugin and v1 are as they were before the install.

## Build provenance

The current `dist\` was built on 2026-10-04 with `tools\build.ps1 -Clean -All` (fresh `build\` dir),
from branch `feat/phase1-natives` at commit `fe05738`. The commits after it change only documentation.
Toolchain: VS 2022 Community, MSVC 19.40.33811 (toolset 14.40.33807), CMake 4.1.0,
RED4ext.SDK 1.0.0 (`a4a7810`). The build produced 0 compiler or MSBuild warnings.

| file | bytes | SHA256 |
|---|---|---|
| `CP2077CoopNet.dll` | 403456 | `06A895004349FA43049DDAC8983758B0A701EB96725B039DE7E8FB2A2253FC1D` |
| `Scripts\Helpers.reds` | 2510 | `706B92EF51C7EE9A3E376E85390D7B347FC13D8C33D61FC863488B8B716B4DC0` |
| `Scripts\Natives.reds` | 1530 | `AB2BF145D5A080FF99E262DABBA6DC022C2756F9F0CF1EC9619D5CFEBE7D9331` |

MSVC stamps each build, so a rebuild gives a different DLL hash. `build.ps1` prints the new hashes
when it stages `dist\`.

Offline checks in that run, all passing:
- `coopnet_tests.exe`: 105 checks, 0 failures. They include Net_NowMs against known FILETIME dates,
  agreement with `system_clock`, no backward step over 200,000 calls (36.5 ns per call), and 60 ms
  of sleep measured as 60.458 ms against `steady_clock` 60.457 ms. They also check the
  Net_Version format and both forms of the startup summary line.
- `verify_exports.py`: Main, Query and Supports exported, and `Supports()` returns 1. Imports are
  kernel32, user32, version and ws2_32 only. All 10 native names are in the image and agree with
  `LoadReport.hpp`, `Main.cpp` and `Natives.reds`. The image holds `CP2077CoopNet 0.1.1 proto 1`,
  and the marker `registered Net_* natives` occurs exactly once.
- `coopnet_plugin_probe.exe` (LoadLibrary + Query): `version=0.1.1 sdk=1.0.0 runtime=3.0.80.51928`, PROBE PASS.
- `run_loopback.py`: the clean, transatlantic and hostile profiles all pass.
- `test_relay_protocol.py`: 10/10. `test_lua_helper.py`: 22/22 under LuaJIT 2.1.
- Redscript: the shipped `Natives.reds` and `Helpers.reds` compile with `scc_check.py` next to
  Jakub's CP2077Coop scripts and Codeware.
- The relay command from step 3 was rehearsed offline on port 11779 with
  `coopnet_loopback.exe 127.0.0.1 11779 400 90`: LOOPBACK PASS.

Not verified yet, and the reason for this guide: loading inside the game, RTTI registration, CET and
redscript lookup of the natives, and `sdk->scripts->Add` accepting the folder.
