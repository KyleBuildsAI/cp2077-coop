# CP2077CoopNet (prototype)

A RED4ext plugin for Cyberpunk 2077 2.31 (RED4ext 1.30) that gives redscript and CET Lua a real
network transport: protocol v2 to the relay repo's `relay_v2.py`, a FIFO inbox, unreliable
(newest wins) and reliable (exactly once, in order) script messages, 30 Hz player snapshots, and
remote players rendered 100-150 ms in the past with interpolation and bounded extrapolation. It
replaces the "latest position only, extra bits squeezed into the forward vector" limit of
`CP2077Coop.dll`.

Version 0.2.0-alpha.4 (wire protocol v2.1, magic 0xCB77).
Status: builds, unit and integration tests pass outside the game, and every end-to-end scenario of
the relay repo passes with C++ clients built from the plugin's protocol modules on both ends.
0.1.0 was loaded in both bench games on 2026-10-04 (Phase 1 go: `Game.Net_*` callable from CET,
about 1 % unreliable loss at 1 % simulated, 0 reliable order errors, 0.03 ms per frame to drain
`Net_Poll`, no crash on load, save load or quit). **0.1.1, 0.1.2 and the 0.2.0 alphas have not been
loaded in the game yet.** [INSTALL_PHASE2.md](INSTALL_PHASE2.md) is the install and in-game check for
this build (next to Jakub's DLL, `relay_v2.py` on 11778, CET commands); it has not been carried out.
[INSTALL_PHASE1.md](INSTALL_PHASE1.md) is the Phase 1 guide for 0.1.2 (CPN2 framing, port 11779).

**Phase 2 (one wire format, v2).** The plugin moved from its own CPN2 framing to protocol v2 from
the relay repo (`coopnet/proto.py`, `include/coop_proto_v2.h`). Each milestone bumps the version to
0.2.0-alpha.N:
1. alpha.1: the C++ codec in `src/v2`, see [Protocol v2 codec](#protocol-v2-codec-srcv2).
2. alpha.2: the per-hop link (port of `reliability.py`) and the relay clock estimate, see
   [Protocol v2 link and clock](#protocol-v2-link-and-clock-srcv2).
3. alpha.3: the snapshot buffer (port of `interp.py`), the transport switch to v2 and the new
   natives `Net_ConnectV2`, `Net_PushPlayer` and `Net_SampleRemote`. See
   [Transport](#transport-protocol-v2) and [Snapshot buffer](#snapshot-buffer-srcv2).
4. alpha.4 (this build): end to end offline. A C++ client built from the plugin's protocol modules
   runs every `run_demo.py` scenario through `relay_v2.py` as the host, as the joiner and on both
   ends, including the clean, US and transatlantic link profiles over a scripted walk, run, sprint
   and turn course. See [End-to-end demo](#end-to-end-demo-c-client).

0.2.0-alpha.4 in short:
- `tests/V2DemoClient.cpp` (`coopnet_v2_demo_client`) is a drop-in for the relay's `client_v2.py`
  on the plugin's codec, delta snapshots, `Connection`, `ClockSync` and `SnapshotBuffer`.
  `tests/V2DemoWorld.cpp` ports the relay's test world (CPython's Mersenne Twister included) and the
  new scripted course. `tools/run_v2_demo.py` runs the pairings and prints the measurements;
  `build.ps1 -Demo` (and `-All`) runs it.
- Relay repo changes (branch `feat/phase2-v2`): the course scenarios and link profiles, a client
  executable per role in `run_demo.py`, a drain at the end of every run so a reliable stream is never
  cut off while it is being repaired, and a fix in `relay_v2.py`: rate limits are now charged when a
  reliable message first arrives, so a burst released by a repaired gap is no longer dropped.
- No plugin code changed apart from the version: the DLL is 0.2.0-alpha.3's code under a new version.

0.2.0-alpha.3 in short:
- `Transport` runs the v2 cookie handshake (room, room key hash, role), the v2 link, the relay
  clock and the peer list from the relay. `Net_Send`/`Net_Poll` ride on `SCRIPT_MSG`, so the 0.1.x
  API keeps working. `Net_Version` says `proto 2.1`.
- `src/core/Protocol.*`, `src/core/Reliability.*` (CPN2) and `tools/coopnet_relay.py` are removed.
  The relay is `relay_v2.py`, which serves Jakub's v1 CP1 clients on the same port.
- Three fixes found while testing the switch: Winsock now stays initialised for the process (a
  cancelled DNS lookup could crash inside WS2_32 after the last `WSACleanup`; this predates 0.2.0),
  DATA packets are paced below `relay_v2.py`'s 120 packets/s limit, and a teleport flag survives
  when a newer player sample replaces an unsent one.

Earlier: 0.1.2 fixed the five confirmed findings of the Phase 1 review (registration checks in the
startup line, copy-assigned String results, a cancellable DNS lookup, two INSTALL_PHASE1.md fixes).

## Layout

```
src/core/Transport.*    protocol v2 client: Winsock thread, handshake, link, relay clock, peers,
                        inbox/outbox, player snapshots and remote player buffers, stats
src/core/Clock.*        Net_NowMs clock (GetSystemTimePreciseAsFileTime -> Unix epoch ms)
src/core/Version.hpp    plugin version (from CMake project VERSION), wire protocol, Net_Version string
src/core/LoadReport.*   the native list and the one-line startup summary
src/core/NativeRegistration.hpp, ScriptString.hpp   registration checks, String result assignment
src/v2/coop_proto_v2.h  protocol v2 wire structs, a verbatim copy of relay/include/coop_proto_v2.h
src/v2/V2Codec.*        v2 packet header, cookie handshake, message framing, every message body
src/v2/V2Delta.*        delta entity snapshots: encoder (acked baselines, byte budget) and decoder
src/v2/V2Hash.*         SHA-256, HMAC-SHA256, room key hash, mod list hash, game build id, cookies
src/v2/V2Describe.*     canonical text for decoded datagrams (golden vectors, diagnostics)
src/v2/V2Reliability.*  per-hop link: packet acks, RTT/RTO, unreliable and reliable ordered messages
src/v2/ClockSync.*      relay clock offset from TIME_REQ/TIME_RESP (interval intersection)
src/v2/SnapshotBuffer.* remote object interpolation buffer (port of interp.py's InterpBuffer)
src/plugin/Main.cpp     RED4ext exports (Query/Main/Supports), Net_* natives, runtime check
scripts/CP2077CoopNet/  Natives.reds (declarations) + Helpers.reds (parsing, roles, flags, poses,
                        self test)
lua/coopnet.lua         CET helper module
tools/build.ps1, fetch_deps.ps1, verify_exports.py   build, dependency fetch, export verification
tools/run_loopback.py   two plugin Transports through relay_v2.py (profiles, relay restart)
tools/v2_golden.py      golden vectors between the C++ v2 codec and the relay's proto.py, both ways
tools/v2_link_trace.py  records reliability.py in its test scenarios and replays the calls on the C++ link
tools/v2_interp_trace.py records interp.py's InterpBuffer and replays the calls on SnapshotBuffer
tools/run_v2_loopback.py two C++ v2 test clients through relay_v2.py over UDP
tools/run_v2_demo.py    every run_demo.py scenario with the C++ demo client as host, joiner or both
tests/                  unit tests (with a scripted fake relay), the Transport loopback, the plugin
                        probe, the Lua helper test, and the v2 codec, link, clock and interp tests
tests/V2Demo*           the C++ demo client (client_v2.py's role), its port of testworld.py, JSON
INSTALL_PHASE2.md       install next to Jakub's DLL, relay_v2.py on 11778, CET checks (not done yet)
```

## Build

Requirements: VS 2022 (MSVC v143), CMake 3.21+, Python 3.12+ with `pefile`, git, and the relay repo
checked out next to this one (`..\relay`, or `COOPNET_RELAY_DIR`): the golden vectors and trace
replays run its `proto.py`, `reliability.py` and `interp.py`, and the loopbacks run its
`relay_v2.py`.

```powershell
powershell -ExecutionPolicy Bypass -File tools\fetch_deps.ps1   # RED4ext.SDK pinned to tag 1.0.0
powershell -ExecutionPolicy Bypass -File tools\build.ps1 -Loopback
powershell -ExecutionPolicy Bypass -File tools\build.ps1 -Clean -All   # fresh build dir, every offline test
```

`-Clean` deletes `build\` first. `-Demo` runs the relay's `run_demo.py` scenarios with its Python
clients (the reference, about 5 minutes) and then with the C++ demo client as host, joiner and on
both ends (`tools\run_v2_demo.py`, about 25 minutes). `-All` implies `-Loopback` and `-Demo` and adds
`tests\test_lua_helper.py`, the relay's unit tests and its `tools\check_c_header.py` to the run; the Lua test needs `lupa` (`pip install lupa`, or
`PYTHONPATH` pointing at a folder that has it). The version lives in one place,
`project(CP2077CoopNet VERSION ...)` plus `COOPNET_PRERELEASE_TYPE` and `COOPNET_PRERELEASE_NUMBER`
in `CMakeLists.txt`: `Query()` (as a RED4ext pre-release), `Net_Version()`, `Net_Stats` and the
probe all read it. The wire protocol in `Net_Version` comes from `COOPNET_WIRE_MAJOR/MINOR` in
`src/core/Version.hpp`, checked against `coop_proto_v2.h` at compile time.

`build.ps1` runs these commands:

```powershell
cmake -S . -B build -G "Visual Studio 17 2022" -A x64 "-DCMAKE_GENERATOR_INSTANCE=C:/Program Files/Microsoft Visual Studio/2022/Community"
cmake --build build --config Release --parallel
build\Release\coopnet_tests.exe
python tools\verify_exports.py build\Release\CP2077CoopNet.dll
build\Release\coopnet_plugin_probe.exe build\Release\CP2077CoopNet.dll
build\Release\coopnet_v2_tests.exe
build\Release\coopnet_v2_fuzz.exe 20000
build\Release\coopnet_v2_fuzz_asan.exe 20000          # when the MSVC ASan runtime is installed
python tools\v2_golden.py run --exe build\Release\coopnet_v2_golden.exe
build\Release\coopnet_v2_reliability_tests.exe
python tools\v2_link_trace.py run --exe build\Release\coopnet_v2_reliability_tests.exe
build\Release\coopnet_v2_clock_tests.exe
build\Release\coopnet_v2_interp_tests.exe
python tools\v2_interp_trace.py run --exe build\Release\coopnet_v2_interp_tests.exe
python tools\run_loopback.py --exe build\Release\coopnet_loopback.exe                        # -Loopback
python tools\run_v2_loopback.py --exe build\Release\coopnet_v2_loopback_client.exe          # -Loopback
python ..\relay\run_demo.py --out build\run_demo_python                                      # -Demo
python tools\run_v2_demo.py --exe build\Release\coopnet_v2_demo_client.exe                   # -Demo
python tests\test_lua_helper.py                                                              # -All
python -m unittest discover -s tests            # in the relay checkout                      # -All
python ..\relay\tools\check_c_header.py                                                      # -All
```

It then recreates `dist\red4ext\plugins\CP2077CoopNet\` and prints each staged file's SHA256.
`CMAKE_GENERATOR_INSTANCE` pins the Community install. Without it, CMake may pick another VS 2022
instance, such as Preview. The CRT is linked statically, so the DLL imports only kernel32, user32,
version and ws2_32.

The redscript files are compile-checked with `MP=Jakub\coop-tools\scc_check.py` against a sandbox
copy of a 2.31 game's `engine\tools`, `r6\cache\final.redscripts` and Codeware's `Scripts` (never
the game folder itself):

```
<sandbox>\engine\tools\scc_lib.dll, scc.exe       copied from the game
<sandbox>\r6\cache\final.redscripts               copied from the game
<sandbox>\r6\scripts\                             empty
<sandbox>\red4ext\plugins\Codeware\Scripts\*.reds copied from the game
<sandbox>\red4ext\plugins\CP2077CoopNet\Scripts\  Natives.reds, Helpers.reds
python "D:\Downloads\syncfix\MP=Jakub\coop-tools\scc_check.py" <sandbox>
```

## Install

[INSTALL_PHASE2.md](INSTALL_PHASE2.md) has the full bench procedure for this build (both games
closed, next to Jakub's `CP2077Coop.dll`, `relay_v2.py` in place of `coop_relay.py`, log lines and
CET console checks). It has not been carried out yet. The layout:

```
<game>\red4ext\plugins\CP2077CoopNet\CP2077CoopNet.dll
<game>\red4ext\plugins\CP2077CoopNet\Scripts\Natives.reds
<game>\red4ext\plugins\CP2077CoopNet\Scripts\Helpers.reds
```

On load, the plugin registers its `Scripts` folder through `sdk->scripts->Add`, so the declarations
are only compiled when the DLL is actually loaded. Do not also copy them into `r6\scripts`,
because that duplicates the declarations. The plugin refuses to load (`Main` returns false) unless
the game executable is file version 3.0.80.51928 (2.31). `Query` reports runtime 2.31 and SDK 1.0.0,
and `Supports` returns API v1. These are the same values as Jakub's `CP2077Coop.dll`, which RED4ext
1.30 loads.

At RTTI post-register, the plugin writes one line to its own log,
`<game>\red4ext\logs\cp2077coopnet-<timestamp>.log`. A native counts as registered only when every
parameter type and the return type resolved in RTTI, and looking its name up again after
`RegisterFunction` returned the function the plugin created (`src/core/NativeRegistration.hpp`):

```
CP2077CoopNet 0.2.0-alpha.4 proto 2.1: registered Net_* natives (13/13): Net_Connect, Net_ConnectRoom, Net_Disconnect, Net_Send, Net_SendTo, Net_Poll, Net_Stats, Net_LocalId, Net_NowMs, Net_Version, Net_ConnectV2, Net_PushPlayer, Net_SampleRemote; scripts added: <game>\red4ext\plugins\CP2077CoopNet\Scripts
```

If a native or the Scripts folder failed, the same line is logged at error level with
`MISSING: Net_X (<failed step>)` or `scripts NOT added: <reason>`. The line does not prove that a
call works; `print(Game.CoopNet_SelfTest())` in the CET console calls the natives from redscript and
prints `redscript ok: CP2077CoopNet 0.2.0-alpha.4 proto 2.1, Net_NowMs=<n>, clock ok, pose parse ok,
Net_SampleRemote(1)=''` while not connected.

String results (`Net_Poll`, `Net_Stats`, `Net_Version`, `Net_SampleRemote`) are copy-assigned into
the caller's slot (`src/core/ScriptString.hpp`). The SDK's `CString` move assignment does not free
the slot's old buffer, so a redscript loop such as `let raw = Net_Poll();` would leak one buffer per
message.

## Relay

The plugin needs `relay_v2.py` from the relay repo. It speaks protocol v2 and Jakub's v1 CP1/RP1 text
on the same UDP port, so v1 keeps working when it replaces the bench's `coop_relay.py`:

```powershell
cd ..\relay
python relay_v2.py                                          # 127.0.0.1:11778
python relay_v2.py --host 0.0.0.0 --port 11778              # reachable from the LAN or internet
python relay_v2.py --latency-ms 95 --jitter-ms 40 --loss-pct 1    # bench link: 115 +-20 ms one way, 1 % loss
python relay_v2.py --latency-ms 20 --jitter-ms 20 --loss-pct 1    # US link: 30 +-10 ms one way, 1 % loss
```

Its link simulation delays, jitters, drops or duplicates every datagram it sends (each client's
downlink); the jitter is uniform in `[latency, latency + jitter]`, so the two lines above give the
transatlantic and US profiles of the end-to-end demo between the two players. Rooms hold 2 players unless `--room-size` says otherwise. Jakub's Warsaw relay only
forwards `CP1,...` text packets and cannot carry protocol v2.

## API

| native | notes | CET helper (`lua/coopnet.lua`) |
|---|---|---|
| `Net_Connect(host: String, port: Int32) -> Bool` | room `default`, no key, role any | `CoopNet.connect(host, port)` |
| `Net_ConnectRoom(host, port, room: String) -> Bool` | no key, role any | `CoopNet.connect(host, port, room)` |
| `Net_ConnectV2(host, port, room, key: String, role: Int32) -> Bool` | 0.2.0-alpha.3 | `CoopNet.connectV2(host, port, room, key, "host")` |
| `Net_Disconnect()` | | `CoopNet.disconnect()` |
| `Net_Send(channel: Int32, payload: String) -> Bool` | to every other player | `CoopNet.send` |
| `Net_SendTo(peer: Int32, channel: Int32, payload: String) -> Bool` | one peer (255 = all) | `CoopNet.sendTo` |
| `Net_Poll() -> String` | `""` when empty | `CoopNet.poll(handler)`, `pollTimed` |
| `Net_Stats() -> String` | JSON | `CoopNet.stats()` decodes it |
| `Net_LocalId() -> Int32` | peer id, 0 until welcomed | `CoopNet.localId()` |
| `Net_NowMs() -> Double` | ms since the Unix epoch (UTC), sub-ms fraction | `CoopNet.nowMs()` |
| `Net_Version() -> String` | `"CP2077CoopNet 0.2.0-alpha.4 proto 2.1"` | `CoopNet.version()`, `parseVersion` |
| `Net_PushPlayer(x, y, z, yaw, pitch, vx, vy, vz: Float, moveState, flags, health: Int32) -> Bool` | 0.2.0-alpha.3 | `CoopNet.pushPlayer(state)` |
| `Net_SampleRemote(peer: Int32) -> String` | 0.2.0-alpha.3, `""` until that player sent something | `CoopNet.sampleRemote(peer)` |

Redscript helpers (`Helpers.reds`): `CoopNet_ParseMessage`, `CoopNet_PollAll`, `CoopNet_ElapsedMs`,
`CoopNet_PushPlayer(position: Vector4, yaw, pitch, velocity: Vector4, moveState, flags, health)`,
`CoopNet_SampleRemote(peer, out pose: CoopNetPose)`, `CoopNet_ParsePose`, `CoopNet_Role*`,
`CoopNet_Flag*`, `CoopNet_DefaultPort()` and `CoopNet_SelfTest()`.

**Choices made for this milestone:**
* **`Net_ConnectV2` instead of a 5-parameter `Net_ConnectRoom`.** RTTI holds one native per name,
  so the old 3-parameter signature could only survive as optional parameters, and whether CET and
  redscript pass omitted optional parameters to a RED4ext native correctly has not been tried in the
  game. A new name keeps `Net_Connect`/`Net_ConnectRoom` byte-for-byte compatible (Phase 1 tooling
  and console commands keep working); both now connect with an empty key and role any.
* **`Net_SampleRemote` returns a String, not `array<Float>`.** CET hands a String to Lua as a plain
  string (an array would become a userdata or table conversion that has not been tried), and the
  String result reuses the copy-assigned return path the other natives use; an array result would
  need a buffer from the game's allocator. One call returns one consistent pose. Parsing costs one
  `gmatch` in Lua or one `StrSplit` in redscript per peer and frame.
* **Health is a `Net_PushPlayer` parameter** although the milestone list did not name it: the wire
  snapshot always carries it, Phase 3 needs it, and a native signature cannot grow later.

Details:
* **Net_ConnectV2**: `room` is 1..32 of `A-Z a-z 0-9 _ -`; `key` is the room password (`""` for
  none, at most 256 bytes; only `SHA-256("cp2077coop-v2|room|key")[0..16)` is sent); `role` 0 any (the
  relay picks host when the room has none), 1 host, 2 joiner, 3 spectator. Invalid arguments return
  false. The first client creates the room and its key.
* **Net_NowMs** reads `GetSystemTimePreciseAsFileTime` (100 ns ticks) and returns a Double, not an
  Int64: CET hands Int64 to Lua as LuaJIT cdata, while a Double is a plain Lua number. It is wall-clock
  time (it follows Windows clock adjustments), shared by two instances on one PC, which is what the
  scoreboard needs. It is not the relay clock; snapshots use the relay clock internally.
* **Net_Version** is `"CP2077CoopNet <major.minor.patch>[-<alpha|beta|rc>.<n>] proto <wire>"`. The
  wire is `2.1` from 0.2.0-alpha.3 and was `1` (CPN2) before. `CoopNet.parseVersion` returns `proto`
  (2) and `protoMinor` (1) and still parses the older strings.
* **Channels**: 1..15 are unreliable, newest wins: the transport stamps an 8-bit per-channel counter
  into `SCRIPT_MSG.flags`, and the receiver drops a message older than (or equal to) one it already
  delivered on that channel from that sender. 16..31 are reliable and ordered (one ordered stream per
  hop). Channel 0 is reserved for transport events.
* **Payload**: at most 1000 bytes of UTF-8 without NUL (the `SCRIPT_MSG` limit; it was 1180 bytes
  under CPN2). Invalid payloads make `Net_Send` return false.
* **Send returns false** when the channel, payload or target is invalid, the transport is not
  connected, no other player can receive script messages (none in the room, or only bridged v1
  players), or the outbox (4096) is full. A target that is not in the room is dropped on the network
  thread and counted as `droppedNoPeer`.
* **Poll format**: `"<senderId>|<channel>|<payload>"`, oldest first. Transport events use sender 0
  and channel 0:

  | event | meaning |
  |---|---|
  | `connecting <ip>:<port>` | the host name resolved, the handshake starts |
  | `no_answer` | no reply to the handshake within 1.5 s; the transport keeps trying. The fallback signal for `NET_MODE=auto` (Total Sync Plan, Rules) |
  | `welcome <id> <role>` | joined the room as peer `<id>` (`host`, `joiner` or `spectator`) |
  | `peer_join <id> <role>[ legacy]` | another player; `legacy` marks a bridged v1 client (no script messages) |
  | `peer_leave <id> <reason>` | `quit`, `timeout`, `kicked`, `rate_limit`, `protocol_error`, `server_shutdown`, `slow_consumer`, `left`, `replaced` or `relay_lost` |
  | `relay_lost` | no datagram from the relay for 5 s; reconnecting and asking to resume the same peer id |
  | `relay_disconnect <reason>` | the relay ended the session: `timeout`, `server_shutdown` and `slow_consumer` reconnect, the others stop with an error |
  | `rejected <reason> <text>` | the relay refused the join: `bad_key`, `role_taken`, `room_full`, `version`, `mod_mismatch`, `game_build`, `server_full`, ... |
  | `error <text>`, `disconnected` | as before |

  `welcome` and `peer_join` gained a role after the id, so a `^welcome (%d+)` pattern still matches.
* **Net_PushPlayer** stores the newest local player; the network thread sends it as a
  `PLAYER_SNAPSHOT` at most every 0.75 / `player_hz` s (25 ms with the relay's 30 Hz), stamped with
  the relay clock at the time of the call. Call it at 30 Hz. It returns false until the session is up
  and the relay clock synced (about 0.2 s after `welcome` on a LAN), or when the state is invalid:
  non-finite, outside the world (|x|, |y| <= 20 km, |z| <= 5 km), move state above 13, flags above
  0xFFFF or with `DRIVING` (vehicles come in Phase 4), health above 255. Pitch is clamped to +-90
  degrees and velocity to +-327 m/s on the wire. `TELEPORTED` (32768) tells receivers not to
  interpolate across that sample; a newer push that replaces an unsent teleport sample keeps the flag.
* **Net_SampleRemote(peer)** renders that player from its snapshot buffer at
  `render time = relay now - (fastest transit + adaptive delay 100..150 ms)` and returns
  `"<mode> <x> <y> <z> <yaw> <pitch> <vx> <vy> <vz> <moveState> <flags> <health> <delayMs> <aheadMs>"`:
  mode `interpolated`, `extrapolated` (past the newest sample, at most 250 ms), `held` (extrapolation
  capped) or `early` (before the first sample after a spawn or teleport, back-extrapolated; a proxy
  should stay hidden then); positions to the millimetre; yaw 0..360; `moveState`, `flags`, `health` of
  the sample at or before the render time; `delayMs` how far in the past it is drawn; `aheadMs` render
  time minus the newest sample. Call it once per frame and peer: each call moves the playout point by
  at most 10 % of the time since the previous call. It returns `""` for an unknown peer, before that
  player's first snapshot, and before the relay clock is synced.
* **Thread safety**: natives can be called from any thread. They only touch mutex-protected queues,
  the remote player buffers and atomics. The network thread owns the socket and the protocol state.
  Host resolution happens on that thread as an overlapped `GetAddrInfoExW` that a stop request
  cancels, so neither connecting, disconnecting, reconnecting nor unloading waits for a slow DNS
  server.

```lua
local CoopNet = require("coopnet")
CoopNet.connectV2("127.0.0.1", 11778, "bench", "secret", "host")   -- the other game: "joiner"
registerForEvent("onUpdate", function()
    CoopNet.poll(function(sender, channel, payload)
        -- channel 0: transport events ("no_answer" = fall back to v1), 1..15 snapshots, 16..31 events
    end)
    CoopNet.pushPlayer({ x = pos.x, y = pos.y, z = pos.z, yaw = yaw, vx = vel.x, vy = vel.y, vz = vel.z,
                         move = CoopNet.MOVE.run, flags = CoopNet.FLAG.weaponDrawn, health = 255 })  -- at 30 Hz
    local pose = CoopNet.sampleRemote(otherPeerId)      -- every frame
    if pose then moveAvatarTo(pose.x, pose.y, pose.z, pose.yaw) end
end)
CoopNet.send(CoopNet.EVENT, "weapon|draw|Items.Preset_Overture_Default")
```

## Transport (protocol v2)

`src/core/Transport.*`, one network thread per connection. What it does on the wire:

* **Handshake**: HELLO (zero-padded to 240 bytes) every 250 ms until a CHALLENGE, then AUTH with the
  cookie and the room key hash every 250 ms until WELCOME or REJECT. A `bad_cookie` REJECT or 5 s
  without a WELCOME starts over with HELLO. Join info: minor 1, the requested role, caps `PLAYER`,
  game build `FNV-1a("2.31a")`, mod list `CP2077CoopNet@<semver>`, name `CP2077CoopNet`, and the last
  session token as `resume_token` after a relay loss (the relay then hands back the same peer id).
* **Session**: the per-hop `Connection` (`V2Reliability`) and `ClockSync` from alpha.2. TIME_REQ at
  10 Hz until 16 exchanges, then 2 Hz; the request is created right before its packet leaves.
  Membership comes only from the relay's `PEER_JOINED`/`PEER_LEFT`; a second `PEER_JOINED` for a known
  id (a peer that resumed) resets that peer's streams. `LINK_STATS` give each peer's relay round trip
  and loss for `Net_Stats`. Other v2 gameplay messages from Python clients are counted and ignored for
  now.
* **Packets**: everything waiting goes out in as few DATA packets as possible, at most one burst
  every 10 ms (about 100 packets/s): `relay_v2.py` counts every packet against 120/s (burst 240) and
  kicks a client after 200 violations in 10 s. Acks go out after 20 ms, keepalives after 1 s of
  silence. Reliable script messages are released into the link at 50/s (burst 100), below the relay's
  reliable bucket (60/s, burst 120); the rest waits in a 4096-message backlog. The loop waits on a
  high-resolution waitable timer, because `WaitForMultipleObjects` timeouts follow the 15.6 ms system
  tick.
* **Loss of the relay**: no datagram for 5 s means `relay_lost`: every peer leaves, unsent and
  unacknowledged messages are dropped (the peers restart their streams too) and the handshake starts
  over with `resume_token`. A relay DISCONNECT for `timeout`, `server_shutdown` or `slow_consumer`
  does the same without resuming; `kicked`, `rate_limit` and `protocol_error` stop with an error.
* **Inbox**: unreliable messages are dropped when 8192 are waiting; reliable ones were already
  acknowledged to the relay, so they may use twice that before they are dropped (and logged once).
* **Remote players**: one `SnapshotBuffer` (player preset) per peer that sends `PLAYER_SNAPSHOT`,
  fed with the unwrapped `sample_time`, the arrival time on the relay clock, position, velocity, yaw
  and the discrete state; bridged v1 players (`LEGACY` flag) have no velocity and interpolate
  linearly. Snapshots that arrive before the relay clock is synced are dropped and counted. A clock
  step above 20 ms resets every buffer's timing.
* **Winsock** is initialised once for the process and never cleaned up by the plugin: a cancelled
  `GetAddrInfoExW` keeps running on a WS2_32 thread-pool thread after its completion is reported, and
  a `WSACleanup` that dropped the last reference in between crashed that thread inside WS2_32 (seen in
  4 of 11 unit test runs; this was possible since 0.1.2 whenever a disconnect interrupted a lookup).

## End-to-end demo (C++ client)

The Phase 2 exit check: every scenario of the relay repo's `run_demo.py` through `relay_v2.py` over
real UDP on 127.0.0.1, with C++ clients built from the plugin's protocol modules on either or both
ends. Run it with `build.ps1 -Demo` (part of `-All`) or directly:

```powershell
python tools\run_v2_demo.py --exe build\Release\coopnet_v2_demo_client.exe                     # 23 runs, ~25 min
python tools\run_v2_demo.py --exe build\Release\coopnet_v2_demo_client.exe --pairings cpp-both --only course-us
python ..\relay\run_demo.py --host-exe build\Release\coopnet_v2_demo_client.exe               # run_demo itself
```

* **The client.** `coopnet_v2_demo_client` (`tests/V2DemoClient.cpp`) takes `client_v2.py`'s
  arguments, follows its schedule and writes the same JSON report, so `run_demo.py --host-exe` /
  `--joiner-exe` can put it on either end. Every datagram goes through the plugin's `coopnet_v2`
  library: `V2Codec` (handshake, 30 Hz `PLAYER_SNAPSHOT` with the vehicle block on the demo path,
  the scripted reliable events), `V2Delta` (the host's 10 Hz delta `ENTITY_SNAPSHOT` of about 80
  NPCs and vehicles with interest management; the joiner decodes and acks), `Connection`,
  `ClockSync` and `SnapshotBuffer` (60 Hz rendering of the other player and every NPC). Its session
  logic follows `client_v2.py`, not the plugin's `Transport`; `Transport` itself (thread, pacing,
  natives) is covered by `run_loopback.py`.
* **The world.** `tests/V2DemoWorld.cpp` ports the relay's `coopnet/testworld.py`: the entity table
  from seed 7 with CPython's Mersenne Twister (`init_by_array`, 53-bit `random()`, `uniform()`), the
  entity and player ground truth, and the scripted course. Before any run the runner compares a dump
  (`--dump-world`) with `testworld.py`: the table must be identical and the truth within 1e-9, or the
  two clients would measure each other against different paths.
* **Pairings.** `cpp-host` (C++ host, Python joiner), `cpp-joiner` (Python host, C++ joiner) and
  `cpp-both`. `bridge` has no joiner, so it runs with a C++ host only (`cpp-host`, `cpp-both`).
* **Scenarios.** Each client impairs its own uplink and downlink (latency + uniform jitter, loss,
  duplicates), so every relay hop sees the leg model:

  | scenario | per leg | between the players (one way) | s |
  |---|---|---|---|
  | clean | 0 | 0 | 12 |
  | realistic | host 18 + [0, 6] ms, 1 %; joiner 78 + [0, 18] ms, 2 % (RU and LA via Warsaw) | 96-120 ms, 3 % | 25 |
  | stress | 60 + [0, 40] ms, 10 % loss, 2 % duplicates | 120-200 ms, 19 % | 25 |
  | brutal | 90 + [0, 60] ms, 20 % loss, 5 % duplicates | 180-300 ms, 36 % | 25 |
  | bridge | v2 host 18 + [0, 6] ms, 1 %, plus a v1 client in the bridged room | | 12 |
  | course-clean | 0 | 0 | 45 |
  | course-us | 10 + [0, 10] ms, 0.5 % | 30 +-10 ms, 1 % | 45 |
  | course-transatlantic | 47.5 + [0, 20] ms, 0.5 % | 115 +-20 ms, 1 % | 45 |

  `clean` and `realistic` also run two v1 clients in the v1 pool next to v2. The `course-*`
  scenarios run both players over a scripted on-foot loop of 39.6 s instead of the demo circle and
  drive: walk 1.6 m/s, run 4.5 m/s, sprint 7.5 m/s, turns (1.5 m radius at walk and run speed, a
  -60/+120/-60 degree zigzag, 5 m at sprint speed), braking from a sprint, standing, starting again.
  Velocity is continuous; the host and the joiner are 9 s apart on the loop.
* **End of a run.** Events stop 4 s before the end, each reliable stream ends with a
  `"<name> done"` chat, and a client quits only once the relay acked all of its events and the
  other's marker arrived (at most 20 s later). Without this, events still being repaired under the
  brutal profile were counted as lost: the first C++ run of `cpp-both/brutal` had 4 events of 182 in
  flight when it stopped.

**What is measured** (all from the clients' and the relay's reports):
* **Snapshot loss**: 30 Hz `PLAYER_SNAPSHOT`s missing between the first and the last one the
  receiver got, against the simulated loss `1 - (1 - up)(1 - down)`. `run_demo.py`'s own delivery
  check (delivered / sent within 5 points of the expected) also counts the start (the receiver not
  yet in the room) and the end.
* **Reliable violations**: per direction, the positions where the received event stream differs
  from the sent one plus missing or extra events (equip, vehicle, hit, death, time/weather, chat,
  teleport, world facts, mod list, a 30-event burst). Must be 0, and so must the relay's protocol
  violations.
* **Clock**: each client's relay clock estimate minus the truth (both sides read
  `QueryPerformanceCounter`, which is also `time.perf_counter()`). The course scenarios hold the
  plugin's estimator (C++ `ClockSync`) within 5 ms of the truth and of the other client;
  `run_v2_demo.py` also checks every exchange of a C++ client after a 5 s warm-up, and the gap
  between two C++ instances at the same moment. `client_v2.py` keeps `interp.py`'s lowest-RTT
  estimate and its looser bound.
* **Pose error**: the distance between the remote player drawn by `SnapshotBuffer` at the render
  time and the sender's ground truth at that same render time (the deliberate render delay of
  about 100 ms plus the latency floor is not an error), and the yaw difference; per kind of movement
  on the course.

**Results** (`build.ps1 -Clean -All`, 2026-10-04, 0.2.0-alpha.4 at dllproto `d096946`, relay
`a41a95e`): 23 of 23 runs passed (the Python reference: 8 of 8). 9,922 reliable events, 0 order
violations, 0 relay violations, 0 rate drops, every stream drained before the end. Loss = network
loss over the received span (simulated in brackets); clock = final error of host / joiner, and for
C++ clients the largest error over the run and the largest gap between two C++ instances; pose =
p95 error of the joiner's view of the host / the host's view of the joiner.

| pairing | scenario | loss h->j / j->h (sim.) | clock host / joiner (ms) | C++ max / pair (ms) | render delay | pose p95 (mm) | yaw p95 |
|---|---|---|---|---|---|---|---|
| cpp-host | clean | 0.0 / 0.0 % (0) | -0.47 / -0.56 | 0.47 / - | 100 ms | 5 / 15 | 0.03 |
| cpp-host | realistic | 2.6 / 3.2 % (3.0) | -0.63 / -0.69 | 0.70 / - | 198 ms | 6 / 14 | 0.03 |
| cpp-host | stress | 17.7 / 19.0 % (19.0) | +0.86 / -0.27 | 1.44 / - | 225 ms | 5 / 14 | 0.15 |
| cpp-host | brutal | 35.8 / 36.7 % (36.0) | -0.76 / -2.05 | 2.10 / - | 314 ms | 6 / 15 | 1.32 |
| cpp-host | course-clean | 0.0 / 0.0 % (0) | -0.33 / -1.02 | 0.67 / - | 100 ms | 5 / 6 | 0.09 |
| cpp-host | course-us | 1.4 / 0.5 % (1.0) | -0.26 / -1.03 | 0.90 / - | 122 ms | 5 / 6 | 0.09 |
| cpp-host | course-transatlantic | 1.4 / 0.6 % (1.0) | +0.48 / -2.73 | 1.16 / - | 200 ms | 6 / 6 | 0.10 |
| cpp-joiner | clean | 0.0 / 0.0 % (0) | -0.77 / -0.58 | 0.58 / - | 101 ms | 5 / 14 | 0.03 |
| cpp-joiner | realistic | 2.8 / 3.0 % (3.0) | -0.95 / +0.73 | 1.24 / - | 201 ms | 6 / 14 | 0.03 |
| cpp-joiner | stress | 20.2 / 19.6 % (19.0) | -1.50 / +0.46 | 1.68 / - | 228 ms | 6 / 14 | 0.48 |
| cpp-joiner | brutal | 36.6 / 35.9 % (36.0) | +0.07 / +0.78 | 1.16 / - | 317 ms | 6 / 15 | 1.04 |
| cpp-joiner | course-clean | 0.0 / 0.0 % (0) | -0.30 / -0.38 | 0.74 / - | 100 ms | 5 / 6 | 0.10 |
| cpp-joiner | course-us | 0.8 / 1.3 % (1.0) | -1.86 / +0.12 | 1.54 / - | 123 ms | 5 / 6 | 0.10 |
| cpp-joiner | course-transatlantic | 1.0 / 1.4 % (1.0) | -1.22 / +0.77 | 1.01 / - | 200 ms | 6 / 6 | 0.08 |
| cpp-both | clean | 0.0 / 0.0 % (0) | -0.46 / -0.18 | 0.60 / 0.28 | 101 ms | 6 / 16 | 0.03 |
| cpp-both | realistic | 3.2 / 2.9 % (3.0) | -0.09 / +0.85 | 1.33 / 1.91 | 200 ms | 5 / 13 | 0.03 |
| cpp-both | stress | 17.8 / 21.2 % (19.0) | -1.20 / +0.38 | 2.05 / 3.19 | 227 ms | 6 / 15 | 0.12 |
| cpp-both | brutal | 37.9 / 33.3 % (36.0) | -1.21 / +0.89 | 4.58 / 2.56 | 311 ms | 6 / 14 | 1.28 |
| cpp-both | course-clean | 0.0 / 0.0 % (0) | -0.19 / -0.35 | 0.66 / 0.53 | 101 ms | 5 / 6 | 0.09 |
| cpp-both | course-us | 1.1 / 0.7 % (1.0) | -0.15 / -0.76 | 1.37 / 1.21 | 123 ms | 5 / 6 | 0.10 |
| cpp-both | course-transatlantic | 1.1 / 0.5 % (1.0) | -0.53 / +0.62 | 1.90 / 1.96 | 200 ms | 6 / 6 | 0.09 |

`bridge` passed for `cpp-host` and `cpp-both`: the v1 client got the C++ host's position (RP1,
error under 1 mm), flags, time and weather through the relay, and the host rendered the v1 player.
The NPC alignment (joiner's interpolated NPCs against the host's truth) stayed at 8-10 mm p95
(12-27 mm under brutal), and every delta entity snapshot the joiner decoded matched the host's view
exactly. The "pose" column is p95 over all frames; 1.4 mm walking, 4 mm running and 7 mm sprinting
are the Hermite spline's error between 30 Hz samples:

| course, p95 position error (mm) / p95 yaw error (deg) | walk | run | sprint | turn | stop |
|---|---|---|---|---|---|
| clean, all pairings and both views | 1.4-1.5 / 0 | 3.8-4.3 / 0 | 6.4-7.1 / 0 | 4.7-7.3 / 0.14-0.16 | 3.4-4.6 / 0 |
| US, all pairings and both views | 1.3-1.5 / 0 | 3.8-4.3 / 0 | 6.4-6.8 / 0 | 4.6-5.0 / 0.14-0.17 | 3.5-4.8 / 0 |
| transatlantic, all pairings and both views | 1.4-1.5 / 0 | 3.9-4.1 / 0 | 6.5-7.1 / 0 | 4.5-5.9 / 0.14-0.17 | 3.3-5.2 / 0 |

**30-minute soak** (the Phase 2 criterion "zero reliable-order violations in 30 min"):
`run_v2_demo.py --pairings cpp-both --only course-transatlantic --duration 1800`, C++ on both ends
over the transatlantic profile, after the final `-All` run with the same binary. Passed: 26,997
reliable events, 0 order violations, 0 relay violations, 0 rate drops; snapshot loss 1.0 % each way
(1.0 % simulated) over about 54,000 snapshots each way; every C++ relay clock estimate within 3.62 ms of the truth
over the whole run and the two instances within 3.79 ms of each other (0.67 ms at the end); pose
p95 5-6 mm in both views and every kind of movement within 1.4-6.7 mm p95; 17,798 delta entity
snapshots decoded with 0 mismatches and the NPCs within 9 mm p95 over 1.18 million entity frames.
The first soak, run before the final build, found a reporting bug in both clients: the received
sequence span flipped after 32,768 snapshots and reported -152 % loss. Its other results were the
same (26,997 events, 0 violations, clock within 2.72 ms, instances within 3.50 ms).

Reading the numbers:
* The clock criterion (under 5 ms between the two instances) holds in every scenario with C++ on
  both ends: on the course profiles every C++ estimate stayed within 1.90 ms of the truth over the
  whole run and two C++ instances within 1.96 ms of each other; 3.19 ms under stress and 4.58 ms
  (one client) under brutal. An earlier run of the same scenarios (before the sequence-span fix,
  same clock code) reached 6.67 ms under brutal, so brutal is near the limit. The Python client's
  lowest-RTT estimate reached 8.45 ms under brutal in the reference run.
* Loss matches the simulation: under 1 % (US, transatlantic) the received span lost 0.5-1.4 %. The
  `all` loss that `run_demo.py` checks is up to 2.5 % on clean links with a Python client, because a
  Python process starts about 0.3 s later and misses the first snapshots; with C++ on both ends it
  is 0.0 %.
* The render delay is the latency floor plus the adaptive 100-150 ms interpolation delay: 100 ms on a
  clean link, 122-123 ms on the US profile and 200 ms on the transatlantic one. A sprinting player is
  therefore drawn about 1.5 m behind where they really are on the transatlantic link; the error
  columns measure how well the drawn path matches the real path once that delay is accounted for.

## Protocol v2 codec (src/v2)

The C++ port of `relay/coopnet/proto.py` (protocol 2.1) and of the delta encoder in
`relay/coopnet/snapshot.py`. It has no RED4ext or Winsock dependency (`coopnet_v2` static library).
The transport uses it for every datagram since 0.2.0-alpha.3.

* **Wire structs**: `src/v2/coop_proto_v2.h` is byte-identical to the relay's header (checked by
  `tools/v2_golden.py`); the relay's `tools/check_c_header.py` checks that header's 30 struct
  layouts and 130 constants and enum values against proto.py with MSVC.
* **Packets**: `DecodePacket`/`EncodePacket` for the 20-byte header (magic, major 2, type, token,
  seq, ack, ack bits). A v3 datagram reports `VersionMismatch` with its major and type, so a relay
  can answer with a REJECT.
* **Cookie handshake**: HELLO (zero-padded to 240 bytes), CHALLENGE, AUTH (join info + cookie + room
  key hash), WELCOME, REJECT and DISCONNECT bodies; `RoomKeyHash`, `ModListHash`, `GameBuildId`,
  `MakeCookie`/`CheckCookie` (HMAC-SHA256, 10 s, bound to address and nonce) in `V2Hash`.
* **Messages**: `DecodeMessages`/`AppendMessage` for the framing inside DATA (at most 96 messages,
  bodies up to 1174 bytes) and `DecodeBody`/`EncodeBody` for every message type with exactly
  proto.py's validation: finite floats, world bounds (|x|,|y| <= 20 km, |z| <= 5 km), enum and range
  checks, strict UTF-8 text. Encoders decode what they wrote and refuse anything a receiver would
  reject. `MessageSpecs()` is the delivery/sender/route table.
* **Delta entity snapshots**: `ENTITY_SNAPSHOT` records (spawn, pos or pos delta, yaw or quaternion,
  velocity, state, target, weapon, world id, removal) and `V2Delta`'s `DeltaEncoder`/`DeltaDecoder`,
  which produce the same bytes and views as snapshot.py.
* **Protocol 2.1 additions** (also added to the relay on its `feat/phase2-v2` branch):
  * `SCRIPT_MSG` (0x30): `channel u8, flags u8, text_len u16, text` with at most 1000 bytes of UTF-8
    and no NUL. Channels 1..15 travel unreliable and 16..31 reliable (the `Net_Send` channel ids),
    and the reliable bit must match. The relay sends it to the named peer, or to everybody for
    0xFF. It carries script text during bring-up, so the old `Net_Send`/`Net_Poll` API can map onto
    v2.
  * `X_WORLD_ID` (entity extension bit 0x08): a u64 static world id (EntityID hash) for binding
    mirrors. It belongs to the entity's identity, so it travels with the spawn block.
  * The relay only accepts these from peers that negotiated minor 1 and skips 2.0 receivers.
* **Golden vectors, both directions** (`python tools\v2_golden.py run --exe ...`): proto.py writes
  valid datagrams of every packet and message type (its own encoders), 119 handcrafted invalid ones,
  2,400 random mutations with proto.py's verdict, hash and quantizer vectors, the message table and
  constants, and three delta scenarios. C++ must give the same verdicts, describe the same values
  and re-encode every canonical datagram and delta snapshot byte for byte. Then C++ writes its own
  vectors the same way and proto.py checks them. A deliberate one-character bug in the C++
  move-state check was caught by the handcrafted `player-move-14` vector.
* **Fuzzing**: `coopnet_v2_fuzz` feeds 20,000 random, mutated, truncated and oversized datagrams and
  bodies, each in a heap block of exactly its size, and checks that accepted input re-encodes to the
  same values. `coopnet_v2_fuzz_asan` is the same program built with `/fsanitize=address`.
* **Limits**: `ModListHash` folds ASCII case and strips ASCII whitespace only, where proto.py uses
  Python's Unicode rules. The quantizers map NaN to 0 where proto.py raises. `PackQuat` follows
  CPython 3.12+ float summation.

## Protocol v2 link and clock (src/v2)

Also in the `coopnet_v2` library; since 0.2.0-alpha.3 the transport's network thread owns one of
each per session. Times are `double` (seconds for the link, milliseconds for the clock) from a
monotonic clock.

* **`Connection`** (`V2Reliability`) is the port of the relay's `coopnet/reliability.py`: one hop
  (client <-> relay). Every DATA packet has a 16-bit sequence (never 0) and acks the newest packet
  from the other side plus the 32 before it. Unreliable messages go out once. Reliable messages carry
  a 16-bit message sequence, wait in a 256-message window until a packet that held them is acked,
  are resent after RTO = SRTT + max(4 x RTTVAR, 10 ms) + 40 ms (clamped 50 ms to 2 s, backoff up to
  8 x), and are delivered exactly once and in order. A resend always goes out in a new packet with a
  new sequence, so every ack names one transmission. A packet holds at most 96 messages.
  `QueueReliable`, `BuildPackets`, `OnPacket`, `ReliableDue` and the state and counters mirror the
  Python API; `NextReliableDue` (for the thread's wait) and the min/max RTT sample are C++ additions.
* **RTT lesson** from the CPN2 prototype (a cumulative ack after a repaired gap read 9.2 s on a
  200 ms link): an RTT sample is only taken from acks carried by a packet that directly follows the
  previous packet received. After a lost or reordered packet, the first ack through can cover packets
  whose earlier acks were lost, and their apparent RTT includes the time the gap stayed open. In the
  45 % loss test such samples reached 333 ms against a 175 ms clean maximum. `reliability.py` got the
  same rule (relay commit c556b7c, plus the 96-message split in 5f7afcd), so both sides still behave
  identically.
* **Fidelity**: `tools\v2_link_trace.py` records every call and result of `reliability.py`'s
  `Connection` in the scenarios of the relay's `tests/test_reliability.py` (0/10/30/45 % loss with
  duplicates, bursts that fill the window, sequence wrap), a 120 ms-jitter reordering link and edge
  cases (window full, oversized bodies, 96-message packing, bad framing, sequence 0, packets too old
  to judge, bogus acks half the sequence space away). `coopnet_v2_reliability_tests trace` replays
  them: same datagrams byte for byte, same deliveries and verdicts, same state after every step,
  SRTT/RTTVAR/RTO and timestamps to the last bit. Dropping the RTT rule in the C++ code makes the
  replay fail in every scenario.
* **`ClockSync`** estimates relay time = local time + offset from TIME_REQ / TIME_RESP. One exchange
  (t0 local send, t1 relay receive, t2 relay send, t3 local receive) proves t2 - t3 <= offset <=
  t1 - t0 whatever the jitter, because both one-way delays are positive. `interp.py` takes the
  midpoint of the lowest-RTT exchange of the last 16; `ClockSync` intersects the intervals of the last
  32 exchanges instead (Marzullo's algorithm: the region shared by the most intervals, so a bad
  exchange is outvoted). With symmetric jitter the error drops to half the difference between the
  smallest uplink and the smallest downlink delay in the window. Details:
  * The exact local send time is kept per request; the u32 `t0` echo only identifies it. Duplicate,
    unknown or late (over 5 s) responses and impossible stamps (relay hold negative or longer than
    the round trip) are refused.
  * `relay_v2.py` truncates its clock to whole ms, so each interval is widened by 1 ms on the t1
    side, and intervals widen by 200 ppm per second of age for clock-rate differences.
  * u32 relay stamps are unwrapped near t0 + offset; `UnwrapRelayMs` does the same for snapshot
    `sample_time` values.
  * The applied offset follows `interp.py`: it jumps to the estimate during warm-up (16 exchanges),
    then slews at most 2 ms per exchange; changes above 50 ms step, and a step above 20 ms is
    reported so snapshot buffers can reset their timing. `Synced()` after 3 exchanges.
  * Requests go out at 10 Hz until warm, then at 2 Hz (`client_v2.py` uses 1 Hz), which keeps the
    32-exchange window within 16 s.
  * `ErrorBoundMs()` is a hard bound under those assumptions; it has to allow any split of the round
    trip, so on a real link it is about half the lowest round trip. Asymmetric base latency remains
    a bias of half the difference, as for any two-way method.

## Snapshot buffer (src/v2)

`SnapshotBuffer` is the port of `InterpBuffer` in the relay's `coopnet/interp.py`, also in the
`coopnet_v2` library (no Winsock); the transport keeps one per remote player.

* **Timeline**: every snapshot carries `sample_time`, the sender's relay clock when it sampled the
  state. A buffer renders at `relay now - (fastest transit + delay)`: the fastest transit is the
  minimum `arrival - sample_time` of the last 90 samples (the latency floor, which also absorbs a
  residual clock offset), the delay adapts to jitter inside `[min, max]` (player preset 100..150 ms at
  30 Hz, entity preset 150..200 ms at 10 Hz) as `max(2 x send interval, p95 jitter + send interval)`.
  The playout point slews at most 10 % of the frame time per frame and snaps when the target moves by
  more than 250 ms (after a clock step resets the timing).
* **Shape**: between two samples a cubic Hermite spline built from the sent velocities (straight lines
  for samples without velocity), yaw the short way round; past the newest sample linear extrapolation
  for at most 250 ms, then held; before the first sample back-extrapolation along its velocity
  ("early"). A `TELEPORTED` sample clears the history; samples not newer than the oldest buffered one
  are late and dropped; history keeps 2 s.
* **Fidelity**: `tools\v2_interp_trace.py` records `interp.py` in the four `InterpTests` scenarios of
  the relay's `tests/test_interp.py` (circle with 120 ms transit, 40 ms jitter and 10 % loss; bounded
  extrapolation; teleport and late samples; the playout snap), hand-written edge cases and 32 random
  scenarios over both presets and two odd configurations (jitter up to 150 ms, loss up to 40 %,
  duplicates, reordering, route changes, teleports, samples without velocity, yaw wrapping, timing
  resets, frame gaps long enough to hold, samples before the first and after the newest sample,
  timeline origins near 2^32 ms and below zero). `coopnet_v2_interp_tests trace` replays every call and
  compares every result bit for bit: buffer size, counters, oldest and newest time and transit count
  after each push, render times, playout points, target delays, positions, yaw and modes. Python's
  float modulo (`lerp_angle`) and `min`/`max` tie rules are reproduced exactly. Reordering one term of
  the Hermite polynomial produces 52 one-ulp mismatches, so the check is sensitive.
* **C++ additions** (not in `interp.py`, not part of the trace comparison): every sample also carries
  pitch, move state, flags and health; `SampleAt` blends pitch linearly, reports the discrete state of
  the sample at or before the render time, and reports a velocity (the sent velocities blended
  linearly, the newest when extrapolating, zero without velocity).

## Tests (latest run)

`tools\build.ps1 -Clean -All` from a fresh build dir on 2026-10-04 for 0.2.0-alpha.4 (dllproto
`feat/phase2-v2` at `d096946`, relay `feat/phase2-v2` at `a41a95e`): exit 0, no compiler or MSBuild
warnings.
Staged in `dist\red4ext\plugins\CP2077CoopNet\`:

| file | bytes | SHA256 |
|---|---|---|
| `CP2077CoopNet.dll` | 509952 | `7ED5DB9B8DCB8A601AB9E669478C4ACCB3F273B935CFF70FA20D01E1B62B36D3` |
| `Scripts\Helpers.reds` | 6931 | `C47CBF71EB26568C2E5F5714BC78B31FEA0F0DD057B7C7A238FCCED4E57AEB45` |
| `Scripts\Natives.reds` | 3183 | `90AF4CFB3AE4A08BD2E644D607ECCBB143C18BFB36BD36A1C81BDBD7034F1201` |

MSVC stamps each build, so a rebuild gives a different DLL hash.

* **End to end** (new, see [End-to-end demo](#end-to-end-demo-c-client)): the relay's `run_demo.py`
  with its Python clients 8/8 scenarios; `run_v2_demo.py` self test (`SELF TEST PASS`) and world
  check OK (entity table identical, truth within 1e-9) and 23/23 runs with the C++ demo client as
  host, as joiner and on both ends: 9,922 reliable events, 0 order violations, 0 relay violations;
  on the course profiles every C++ relay clock estimate within 1.90 ms of the truth over the whole
  run and two C++ instances within 1.96 ms of each other.
* **30-minute soak** with the same binary (C++ on both ends, transatlantic profile): 26,997 reliable
  events, 0 order violations, clock within 3.62 ms, instances within 3.79 ms (see
  [End-to-end demo](#end-to-end-demo-c-client)).
* `coopnet_tests.exe`: 200 checks, 0 failures. Net_NowMs (FILETIME conversion, agreement with
  `system_clock`, no backward step over 200,000 calls at 36.4 ns per call), the Net_Version format
  (`proto 2.1`), the startup line with 13 natives, the registration checks with a fake RTTI, String
  results into a live slot (100 polls leak nothing; the old move assignment leaks 99), the Transport
  argument checks, stopping while the relay name resolves (0.1-0.2 ms against 1.24 s for a blocking
  lookup), and the v2 handshake and session against a scripted fake relay (8 HELLOs of 240 bytes in
  1.8 s and `no_answer`; `rejected bad_key`; the relay clock synced after 3 TIME_REQ in 199 ms,
  0.24 ms off; script messages; the `PLAYER_SNAPSHOT` fields; a rendered remote player; relay
  shutdown, relay silence with `relay_lost` after 5.000 s and `resume_token`, and a kick).
* `run_loopback.py`: two plugin Transports through `relay_v2.py` (rooms of 3), each profile also
  checking that a wrong key is rejected (`bad_key`) and a second host too (`role_taken`), and that the
  relay counted 0 violations, 0 malformed datagrams, 0 rate drops and 0 kicks:

  | relay link | reliable A->B / B->A | unreliable A->B (60 Hz) | B renders A (30 Hz, 6 m/s circle): error p95 / max, delay p50 | teleport |
  |---|---|---|---|---|
  | clean | 400 / 200 in order | 529 of 529 | 5.3 / 10.3 mm, 100.3 ms, 98.7 % interpolated | 0 frames between |
  | 115 ms + 20 ms jitter, 1 % loss (bench) | 400 / 200 in order | 500 of 534 (20 stale) | 5.2 / 7.4 mm, 217.1 ms, 98.9 % interpolated | 0 frames between |
  | 60 ms + 40 ms jitter, 10 % loss, 2 % dup | 300 / 150 in order | 310 of 415 (63 stale) | 5.2 / 6.7 mm, 163.0 ms, 97.7 % interpolated | 0 frames between |
  | relay restart (20 ms + 5 ms) | rejoined after 9.39 s with the same peer ids | | | |

  The error is measured against the true path at the render time on B's relay clock, so it includes
  the two clients' clock disagreement. A 1000-byte reliable message arrived once in every profile, and
  B saw A leave with reason `quit` within 0.14 s.
* `run_v2_loopback.py`: 3/3 profiles; relay clock error after warm-up at most 0.36, 1.01 and 2.47 ms,
  the two clients within 0.36, 0.88 and 2.79 ms of each other.
* `verify_exports.py`: Main/Query/Supports exported, `Supports()` returns 1, imports kernel32, user32,
  version and ws2_32 only, the 13 native names agree with `LoadReport.hpp`, `Main.cpp` and
  `Natives.reds`, the image holds `CP2077CoopNet 0.2.0-alpha.4 proto 2.1`.
  `coopnet_plugin_probe.exe`: `version=0.2.0 prerelease type=1 number=4 sdk=1.0.0
  runtime=3.0.80.51928`, PROBE PASS.
* `test_lua_helper.py`: 36 checks under LuaJIT 2.1 (mocked `Net_Version` reports 0.2.0-alpha.4).
* Redscript: `Natives.reds` and `Helpers.reds` (unchanged since alpha.3) compile with `scc_check.py`
  in a fresh sandbox (`%TEMP%\cp2077coopnet_scc_alpha4`: `engine\tools`, `final.redscripts` and
  Codeware's `Scripts` copied from Test B): `OK: compiled r6\scripts,
  red4ext\plugins\CP2077CoopNet\Scripts, red4ext\plugins\Codeware\Scripts`.
* Unchanged code, green again: `coopnet_v2_tests.exe` 24,076 checks; `v2_golden.py` 7,920 + 7,447
  checks; `coopnet_v2_fuzz.exe` and `coopnet_v2_fuzz_asan.exe` 20,000 inputs each;
  `coopnet_v2_reliability_tests.exe` 38,493 checks; `v2_link_trace.py` 8 scenarios, 85,424 calls,
  0 mismatches; `coopnet_v2_clock_tests.exe` 9,501 checks; `coopnet_v2_interp_tests.exe` 42 checks;
  `v2_interp_trace.py` 95,279 values, 0 mismatches.
* Relay repo: 94 unit tests OK (78 before; new: the test-world course, two rate-limit tests and the
  received sequence span);
  `check_c_header.py`: 30 structs and 130 constants, 348 checks match.

## Known limits

* Only 0.1.0 has run in the game. The 0.1.1 natives, the 0.1.2 fixes and every 0.2.0 alpha are
  verified offline only; [INSTALL_PHASE2.md](INSTALL_PHASE2.md) is ready but not carried out. Not
  tried in the game yet: the three new natives (in particular `Float` parameters from CET and
  redscript and the 11-parameter `Net_PushPlayer`), `StrSplit` and `StringToFloat` in
  `CoopNet_ParsePose` at runtime (compile-checked only), and `Net_SampleRemote` per frame.
* The end-to-end demo runs the plugin's protocol modules in a test client, not the plugin's
  `Transport`: `Transport` still ignores entity snapshots, equip, hit and the other gameplay messages
  (they are counted), and `Net_PushPlayer` has no vehicle block (`DRIVING` is refused; Phase 4).
  `Transport` is covered by `run_loopback.py` (two Transports through `relay_v2.py`).
* The relay clock stays within 5 ms on the realistic profiles; under the brutal profile (20 % loss,
  60 ms jitter per leg) one run had a C++ client 6.67 ms off and two C++ instances 6.75 ms apart.
* The rate-limit bug in `relay_v2.py` (limits charged when a repaired gap released a burst, so
  acked events could be dropped) is fixed in the relay at `38a59ac`. Relays older than that still
  have it; the transport keeps releasing reliable messages at 50/s (burst 100), below the relay's
  60/s bucket.
* Under heavy loss a lost event waits for its resend with exponential backoff (up to 8 x RTO), so the
  last events of a stream can take several seconds; the demo's drain waits for them.
* Unreliable script messages are newest-wins per channel: with jitter larger than the send interval
  the relay link reorders them and the late ones are dropped (counted as `unrelStale`): 19 of 530
  messages at 60 Hz over 115 ms + 20 ms jitter with 1 % loss.
* `relay_v2.py` is single-threaded Python; a 30-minute run with two clients at 30 Hz was fine, more
  players or a slower machine are untested.
* No encryption. The room key only keeps strangers out of a room (its hash is checked by the relay,
  which must be trusted).
* IPv4 only. One reliable stream per hop, so a lost event delays later events on every reliable
  channel. No fragmentation above 1000 bytes.
* The relay is a Python reference. Jakub's Warsaw relay cannot carry this protocol.
