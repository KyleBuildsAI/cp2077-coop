# CP2077CoopNet 0.2.0-alpha.5: Phase 2 install and in-game check

> **Offline verification complete for alpha.5.** Kyle authorized bench deployment and potato-mode
> live testing on 2026-10-04. Close both test games before installing. Record the actual live results
> separately; this guide and its success checklist do not establish an in-game pass.

Phase 2 moved the plugin to protocol v2 (magic 0xCB77, wire 2.1) from the relay repo. This guide
installs the plugin next to Jakub's `CP2077Coop.dll` in the two bench games, swaps the bench relay
for `relay_v2.py` on the same port (v1 keeps working through it), and lists the log lines and CET
console output that show the v2 path works in the game.

Alpha.5 adds the Phase 2 review repairs and passes the transport/codec tests and both UDP loopback
suites; see README.md, "Tests (latest run)", for current hashes. Its API and wire format are unchanged.

Historical alpha.4 coverage (see README.md, "End-to-end demo"): every `run_demo.py`
scenario of the relay repo with the C++ client (the plugin's protocol modules) as the host, as the
joiner and on both ends, including the clean, US (30 +-10 ms, 1 % loss) and transatlantic
(115 +-20 ms, 1 % loss) link profiles: 0 reliable order violations, the relay clock of two C++
instances within 5 ms of each other, and the interpolated remote player within millimetres of the
sender's true path at the render time.

Bench folders used below (change them if yours differ):

```
G:\SteamLibrary\steamapps\common\Cyberpunk 2077 - Baseline
G:\SteamLibrary\steamapps\common\Cyberpunk 2077 - Test B
```

Requirements on the bench: game 2.31 (exe file version 3.0.80.51928), RED4ext 1.30, CET, Codeware
and Jakub's coop mod. Python 3.11 or newer for the relay. The relay checkout
`D:\Downloads\syncfix\coopnet\relay` must be on branch `feat/phase2-v2` at commit `38a59ac` or later
(the rate-limit fix; `git -C D:\Downloads\syncfix\coopnet\relay log --oneline -1`).

## 1. What gets installed

```
dist\red4ext\plugins\CP2077CoopNet\CP2077CoopNet.dll
dist\red4ext\plugins\CP2077CoopNet\Scripts\Natives.reds
dist\red4ext\plugins\CP2077CoopNet\Scripts\Helpers.reds
```

(`dist` is `D:\Downloads\syncfix\coopnet\dllproto\dist`; "Build provenance" at the end lists the
hashes.) The plugin lives in its own folder **next to** Jakub's plugin:

```
<game>\red4ext\plugins\CP2077Coop\        Jakub's CP2077Coop.dll: untouched, stays installed
<game>\red4ext\plugins\CP2077CoopNet\     this plugin
```

Both DLLs stay installed until Phase 3 is finished (Total Sync Plan, Rules). The plugin hands its own
`Scripts` folder to the redscript compiler on load, so **do not** copy the `.reds` files into
`r6\scripts`: the natives would be declared twice and the whole script compile, Jakub's scripts
included, would fail. If the Phase 1 fallback copy `r6\scripts\CP2077CoopNet` exists, the install
below removes it.

## 2. Install (both games closed)

```powershell
# Must print nothing. If it lists a process, close the games first.
Get-Process Cyberpunk2077 -ErrorAction SilentlyContinue

$src   = 'D:\Downloads\syncfix\coopnet\dllproto\dist\red4ext\plugins\CP2077CoopNet'
$games = 'G:\SteamLibrary\steamapps\common\Cyberpunk 2077 - Baseline',
         'G:\SteamLibrary\steamapps\common\Cyberpunk 2077 - Test B'
foreach ($game in $games) {
    $dest = Join-Path $game 'red4ext\plugins\CP2077CoopNet'
    $fallback = Join-Path $game 'r6\scripts\CP2077CoopNet'
    if (Test-Path $fallback) {
        Remove-Item -Recurse -Force $fallback
        "$game -> removed the r6\scripts\CP2077CoopNet fallback copy"
    }
    # /MIR makes the folder an exact copy of dist (older leftovers go). Exit codes 0-7 mean success.
    robocopy $src $dest /MIR /NJH /NJS /NDL
    Get-ChildItem -Recurse -File $dest | Select-Object FullName, Length
    (Get-FileHash -Algorithm SHA256 (Join-Path $dest 'CP2077CoopNet.dll')).Hash
    "$game -> Jakub's DLL present: $(Test-Path (Join-Path $game 'red4ext\plugins\CP2077Coop\CP2077Coop.dll'))"
    "$game -> r6\scripts\CP2077CoopNet present: $(Test-Path $fallback)"
}
```

Each game must list exactly `CP2077CoopNet.dll`, `Scripts\Helpers.reds` and `Scripts\Natives.reds`,
the DLL hash must match "Build provenance", Jakub's DLL must say `present: True` and the fallback
`present: False`.

Refresh the mod list the coop panel compares, so both instances still match in "mods compared":

```powershell
python 'G:\SteamLibrary\steamapps\common\Cyberpunk 2077 - Baseline\coop-tools\devkit.py' modlist `
    'G:\SteamLibrary\steamapps\common\Cyberpunk 2077 - Baseline' `
    'G:\SteamLibrary\steamapps\common\Cyberpunk 2077 - Test B'
```

Optional offline checks of the installed copy (games may stay closed):

```powershell
cd D:\Downloads\syncfix\coopnet\dllproto
python tools\verify_exports.py 'G:\SteamLibrary\steamapps\common\Cyberpunk 2077 - Baseline\red4ext\plugins\CP2077CoopNet\CP2077CoopNet.dll'
#   ... VERIFY PASS
build\Release\coopnet_plugin_probe.exe 'G:\SteamLibrary\steamapps\common\Cyberpunk 2077 - Baseline\red4ext\plugins\CP2077CoopNet\CP2077CoopNet.dll'
#   version=0.2.0 prerelease type=1 number=5 sdk=1.0.0 runtime=3.0.80.51928 ... PROBE PASS
python 'D:\Downloads\syncfix\MP=Jakub\coop-tools\scc_check.py' 'G:\SteamLibrary\steamapps\common\Cyberpunk 2077 - Baseline'
#   OK: compiled r6\scripts, ... (the list must include red4ext\plugins\CP2077CoopNet\Scripts)
```

Never run `scc.exe` directly; `scc_check.py` uses the game's compiler library without popups.

## 3. Relay: relay_v2.py on 11778 instead of coop_relay.py

`relay_v2.py` serves protocol v2 **and** Jakub's v1 `CP1,...` text on the same UDP port, so it
replaces the bench's `coop_relay.py` on 11778 and v1 keeps working (the relay repo's `run_demo.py`
v1-pool and bridge scenarios check exactly that). v1 clients stay in their own v1 pool; the v2 test
below uses its own room. Stop `coop_relay.py` first (Ctrl+C in its window), then:

```powershell
cd D:\Downloads\syncfix\coopnet\relay
# The transatlantic bench link: one way 95..135 ms (115 +-20) and 1 % loss on every datagram the relay sends.
python relay_v2.py --port 11778 --latency-ms 95 --jitter-ms 40 --loss-pct 1 --log relay_v2_bench.log
```

Other links: `--latency-ms 20 --jitter-ms 20 --loss-pct 1` is the US profile (20..40 ms), no options
is a clean local link. The relay simulates only what it sends (each client's downlink); the delay
between the two players is therefore the simulated one, and the jitter is uniform in
`[latency, latency + jitter]`. Expected right away:

```
HH:MM:SS relay v2 listening on 127.0.0.1:11778 (v1 CP1/RP1 + v2 binary) link sim: +95ms jitter 40ms loss 1.0% dup 0.0% legacy bridge room: 'legacy'
```

Then, when the games connect: `EVENT v1 client joined v1#1 ...` for Jakub's DLL, and for the plugin
`EVENT room created 'bench' ...`, `EVENT join bench/#1 host 'CP2077CoopNet' ... v2.1`,
`EVENT join bench/#2 joiner ...`. A `[STATS]` block follows every 5 s; `violations=0` on both v2
peers is expected. It listens on 127.0.0.1, which is enough for two instances on this PC.

Because only the relay's sends are delayed, each game's relay clock estimate carries the same bias
(half the one-way delay). The two instances still agree on the relay clock, which is what the
snapshot timeline needs; the scoreboard (`sync_audit.py`) uses `Net_NowMs`, which is unaffected.

## 4. Launch both games and read the logs

Start both instances through the main menu. `<game>` is the game folder.

**a) RED4ext loader log**, the newest `<game>\red4ext\logs\red4ext-*.log`:

```
... CP2077CoopNet (version: 0.2.0-alpha.5, author(s): CP2077 Coop) has been loaded
... CP2077CoopNet: '<game>\red4ext\plugins\CP2077CoopNet\Scripts'
```

The same log must still show Jakub's `CP2077Coop` plugin loaded.

**b) The plugin's log**, the newest `<game>\red4ext\logs\cp2077coopnet-<date>-<time>.log`:

```
game file version 3.0.80.51928 (2.31) ok
added <game>\red4ext\plugins\CP2077CoopNet\Scripts to the redscript compilation
CP2077CoopNet 0.2.0-alpha.5 proto 2.1 loaded; the Net_* natives are added at RTTI post-register
CP2077CoopNet 0.2.0-alpha.5 proto 2.1: registered Net_* natives (13/13): Net_Connect, Net_ConnectRoom, Net_Disconnect, Net_Send, Net_SendTo, Net_Poll, Net_Stats, Net_LocalId, Net_NowMs, Net_Version, Net_ConnectV2, Net_PushPlayer, Net_SampleRemote; scripts added: <game>\red4ext\plugins\CP2077CoopNet\Scripts
```

```powershell
foreach ($game in $games) {
    Select-String -Path (Join-Path $game 'red4ext\logs\cp2077coopnet-*.log') -SimpleMatch 'registered Net_* natives (13/13)' |
        Select-Object -Last 1 | ForEach-Object { "$game -> $($_.Line)" }
}
```

**c) Redscript log** `<game>\r6\logs\redscript_rCURRENT.log`: `Natives.reds` and `Helpers.reds` of
CP2077CoopNet among the compiled files, then `Compilation complete`, and no error popup.

## 5. CET console checks

One line at a time in each game's CET console; the expected output follows each command.

**Step 1: natives and redscript (no relay needed)**

```lua
print(Game.Net_Version())
```
`CP2077CoopNet 0.2.0-alpha.5 proto 2.1`

```lua
print(Game.CoopNet_SelfTest())
```
`redscript ok: CP2077CoopNet 0.2.0-alpha.5 proto 2.1, Net_NowMs=<number>, clock ok, pose parse ok, Net_SampleRemote(1)=''`.
This calls `Net_Version`, `Net_NowMs` and `Net_SampleRemote` from redscript and runs `StrSplit` and
`StringToFloat` in `CoopNet_ParsePose`: the first runtime test of those in the game.

**Step 2: connect.** Baseline as the host first, then Test B as the joiner (room `bench`, key
`secret`; roles 1 host, 2 joiner):

```lua
print(Game.Net_ConnectV2("127.0.0.1", 11778, "bench", "secret", 1))   -- Baseline
print(Game.Net_ConnectV2("127.0.0.1", 11778, "bench", "secret", 2))   -- Test B
```
`true` in both. Drain the inbox:

```lua
for i = 1, 64 do local m = Game.Net_Poll() if m == "" then break end print(m) end
```
Baseline:
```
0|0|connecting 127.0.0.1:11778
0|0|welcome 1 host
0|0|peer_join 2 joiner      (once Test B joined; drain again)
```
Test B: `welcome 2 joiner` and `peer_join 1 host`. `0|0|no_answer` means no reply within 1.5 s: the
relay is not running on 11778 (this is the v1 fallback signal for `NET_MODE=auto`).
`0|0|rejected bad_key ...` means the two games used different keys; `rejected role_taken` that a
second host tried to join.

```lua
print(Game.Net_Stats())
```
JSON with `"state":"connected"`, `"role":"host"` (or `joiner`), `"wire":"v2.1"`,
`"version":"CP2077CoopNet 0.2.0-alpha.5 proto 2.1"`, `"relay":"127.0.0.1:11778"`, `"room":"bench"`,
`"clock":{"synced":true,...}` and one entry in `"peers"` for the other game. With the transatlantic
relay link, that peer's `"rttMs"` settles near 230 ms and `"relayRttMs"` near 115 ms.

**Step 3: one reliable message each way**

```lua
print(Game.Net_Send(16, "hello from " .. Game.Net_LocalId()))   -- Baseline: true
```
Drain in Test B: `1|16|hello from 1`. Then send from Test B and drain in Baseline.

**Step 4: player snapshot and the remote pose**

In Baseline, push the player's current position once (yaw, pitch and velocity 0, move state idle,
flags `weaponDrawn`, health 255):

```lua
local p = Game.GetPlayer():GetWorldPosition() print(p.x, p.y, p.z, Game.Net_PushPlayer(p.x, p.y, p.z, 0, 0, 0, 0, 0, 0, 2, 255))
```
The position and `true`. In Test B, about a second later:

```lua
print(Game.Net_SampleRemote(1))
```
`held <x> <y> <z> 0.00 0.00 0.00 0.00 0.00 0 2 255 <delayMs> <aheadMs>` with Baseline's
coordinates (to the millimetre). `held` because only one sample arrived; with 30 Hz pushes from an
`onUpdate` loop it reads `interpolated` with `delayMs` around 100-150 ms plus the link's latency
floor. `""` means nothing arrived from peer 1 yet or the clock is not synced.

**Step 5: drain cost (budget: under 0.1 ms per frame)**

```lua
local t = Game.Net_NowMs() for i = 1, 256 do if Game.Net_Poll() == "" then break end end print(string.format("drain %.4f ms", Game.Net_NowMs() - t))
```

**Step 6: disconnect**

```lua
Game.Net_Disconnect()
```
The other game's next drain shows `0|0|peer_leave <id> quit`; the relay logs `EVENT leave bench/#<id> ... (QUIT)`.

The CET helper `lua\coopnet.lua` wraps the same calls (`CoopNet.connectV2(host, port, room, key,
"host")`, `CoopNet.pushPlayer(state)`, `CoopNet.sampleRemote(peer)`, ...) for the Phase 3 scripts.

## 6. Success checklist

- [ ] Both `red4ext-*.log`: `CP2077CoopNet (version: 0.2.0-alpha.5, ...) has been loaded`, and Jakub's
      `CP2077Coop` still loaded.
- [ ] Both `cp2077coopnet-*.log`: `registered Net_* natives (13/13)` with `scripts added:`.
- [ ] Both redscript logs: the two CP2077CoopNet `.reds` compiled, no error.
- [ ] `Net_Version` and `CoopNet_SelfTest` as in step 1 (including `pose parse ok`).
- [ ] `welcome` and `peer_join` with roles, `Net_Stats` connected with `wire v2.1` and a synced clock,
      a reliable message each way.
- [ ] `Net_PushPlayer` returns `true` and the other game's `Net_SampleRemote(1)` returns that position.
- [ ] The relay shows both v2 peers with `violations=0`, and Jakub's v1 sync still works through it.
- [ ] No crash on load, on loading a save, or on quit.

## 7. If something fails

| What you see | What it means | What to do |
|---|---|---|
| RED4ext log: `Could not load plugin 'CP2077CoopNet'`, no plugin log | Windows could not load the DLL | Check the folder; run the probe from section 2 on the installed copy. |
| `CP2077CoopNet did not initialize properly, unloading...` | `Main` returned false | The plugin log says why, e.g. `unsupported game version`. |
| Summary line at `[error]` with `MISSING: Net_X (<step>)` | That native failed a registration check | Report the line and the `Net_X not registered: ...` line above it. |
| Summary line with `scripts NOT added: ...` | `sdk->scripts->Add` refused the folder | Phase 1 fallback: copy `Scripts\*.reds` to `<game>\r6\scripts\CP2077CoopNet\` **and** delete the plugin's `Scripts` folder (never both). Undo it on uninstall. |
| CET: `attempt to call a nil value (field 'Net_ConnectV2')` | The game runs an older plugin | Check the summary line and the installed DLL hash. |
| `0|0|no_answer` and no `welcome` | Nothing answers on 11778 | Start `relay_v2.py` (section 3); check that `coop_relay.py` is stopped. |
| Jakub's v1 stops syncing after the relay swap | v1 traffic does not reach relay_v2.py | Check the relay's `[STATS]` lines for `v1#1`/`v1#2`; switch back to `coop_relay.py` and report. |
| `Net_PushPlayer` returns `false` | Not connected, clock not yet synced (about 0.2 s after `welcome`), or an invalid state | Check `Net_Stats` (`clock.synced`, `refusedPlayers`). |

## 8. Uninstall

With the games closed:

```powershell
Get-Process Cyberpunk2077 -ErrorAction SilentlyContinue   # must print nothing
foreach ($game in $games) {
    foreach ($folder in 'red4ext\plugins\CP2077CoopNet', 'r6\scripts\CP2077CoopNet') {
        $path = Join-Path $game $folder
        if (Test-Path $path) { Remove-Item -Recurse -Force $path; "$game -> removed $folder" }
        "$game -> $folder present: $(Test-Path $path)"
    }
}
```

Run the `devkit.py modlist` command from section 2 again, and restart `coop_relay.py` if wanted.
Jakub's plugin and v1 are then as before the install.

## Build provenance

See README.md, "Tests (latest run)", for the run that produced `dist\` and the SHA256 of each file.
