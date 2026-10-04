# CP2077CoopNet (prototype)

A RED4ext plugin for Cyberpunk 2077 2.31 (RED4ext 1.30) that gives redscript and CET Lua a real
message transport: a FIFO inbox, an unreliable channel for snapshots, and a reliable channel for
events (resent until acknowledged, delivered once and in order). It replaces the
"latest position only, extra bits squeezed into the forward vector" limit of `CP2077Coop.dll`.

Version 0.1.1 (protocol 1). Status: builds, unit and integration tests pass outside the game.
**It has not been loaded in the game yet.** [INSTALL_PHASE1.md](INSTALL_PHASE1.md) is the Phase 1
install and in-game check: install steps, the bench relay on port 11779, CET console commands and
the log lines that prove success.

## Layout

```
src/core/Protocol.*     frame codec (20-byte header), sequence arithmetic, control payload helpers
src/core/Reliability.*  per-peer reliable stream: send window, SACK acks, resend, reorder buffer
src/core/Transport.*    Winsock UDP thread, peers, inbox/outbox queues, stats (no RED4ext dependency)
src/core/Clock.*        Net_NowMs clock (GetSystemTimePreciseAsFileTime -> Unix epoch ms)
src/core/Version.hpp    plugin version (from CMake project VERSION) and the Net_Version string
src/core/LoadReport.*   the native list and the one-line startup summary
src/plugin/Main.cpp     RED4ext exports (Query/Main/Supports), Net_* natives, runtime check
scripts/CP2077CoopNet/  Natives.reds (declarations) + Helpers.reds (parsing, channel ids, self test)
lua/coopnet.lua         CET helper module
tools/coopnet_relay.py  relay for this protocol, with latency/jitter/loss/reorder simulation
tools/*.ps1, *.py       build, dependency fetch, export verification, loopback runner
tests/                  unit tests, relay loopback test, plugin probe, relay and Lua tests
```

## Build

Requirements: VS 2022 (MSVC v143), CMake 3.21+, Python 3.11+ with `pefile`, git.

```powershell
powershell -ExecutionPolicy Bypass -File tools\fetch_deps.ps1   # RED4ext.SDK pinned to tag 1.0.0
powershell -ExecutionPolicy Bypass -File tools\build.ps1 -Loopback
powershell -ExecutionPolicy Bypass -File tools\build.ps1 -Clean -All   # fresh build dir, every offline test
```

`-Clean` deletes `build\` first. `-All` adds `tests\test_relay_protocol.py` and
`tests\test_lua_helper.py` to the run; the Lua test needs `lupa` (`pip install lupa`, or `PYTHONPATH`
pointing at a folder that has it). The version lives in one place, `project(CP2077CoopNet VERSION ...)`
in `CMakeLists.txt`: `Query()`, `Net_Version()`, `Net_Stats` and the probe all read it.

`build.ps1` runs these commands:

```powershell
cmake -S . -B build -G "Visual Studio 17 2022" -A x64 "-DCMAKE_GENERATOR_INSTANCE=C:/Program Files/Microsoft Visual Studio/2022/Community"
cmake --build build --config Release --parallel
build\Release\coopnet_tests.exe
python tools\verify_exports.py build\Release\CP2077CoopNet.dll
build\Release\coopnet_plugin_probe.exe build\Release\CP2077CoopNet.dll
python tools\run_loopback.py
```

It then recreates `dist\red4ext\plugins\CP2077CoopNet\` and prints each staged file's SHA256. `CMAKE_GENERATOR_INSTANCE` pins the
Community install. Without it, CMake may pick another VS 2022 instance, such as Preview. The CRT
is linked statically, so the DLL imports only kernel32, user32, version and ws2_32.

## Install

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

Once RED4ext has accepted the natives, the plugin writes one line to its own log,
`<game>\red4ext\logs\cp2077coopnet-<timestamp>.log`:

```
CP2077CoopNet 0.1.1 proto 1: registered Net_* natives (10/10): Net_Connect, Net_ConnectRoom, Net_Disconnect, Net_Send, Net_SendTo, Net_Poll, Net_Stats, Net_LocalId, Net_NowMs, Net_Version; scripts added: <game>\red4ext\plugins\CP2077CoopNet\Scripts
```

If a native or the Scripts folder failed, the same line is logged at error level with
`MISSING: ...` or `scripts NOT added: <reason>`. See [INSTALL_PHASE1.md](INSTALL_PHASE1.md).

## API

| native | redscript | CET |
|---|---|---|
| `Net_Connect(host: String, port: Int32) -> Bool` | `Net_Connect("1.2.3.4", 11779)` | `Game.Net_Connect("1.2.3.4", 11779)` |
| `Net_ConnectRoom(host, port, room: String) -> Bool` | | |
| `Net_Disconnect()` | | |
| `Net_Send(channel: Int32, payload: String) -> Bool` | to every peer | |
| `Net_SendTo(peer: Int32, channel: Int32, payload: String) -> Bool` | one peer | |
| `Net_Poll() -> String` | `""` when empty | |
| `Net_Stats() -> String` | JSON | `CoopNet.stats()` decodes it |
| `Net_LocalId() -> Int32` | 0 until welcomed | |
| `Net_NowMs() -> Double` | ms since the Unix epoch (UTC), sub-ms fraction | `CoopNet.nowMs()` |
| `Net_Version() -> String` | `"CP2077CoopNet 0.1.1 proto 1"` | `CoopNet.version()`, `CoopNet.parseVersion(s)` |

* **Net_NowMs** reads `GetSystemTimePreciseAsFileTime` (100 ns ticks) and returns a Double, not an
  Int64: CET hands Int64 to Lua as LuaJIT cdata (`123LL`, built by compiling a chunk per call), while
  a Double is a plain Lua number. A Double keeps every whole millisecond exact and resolves about
  0.25 us at today's values. It is wall-clock time, so it follows Windows clock adjustments. Two
  instances on one PC share it, which is what the scoreboard needs. In redscript, Double literals
  need a `d` suffix (`1.0d`); `CoopNet_ElapsedMs(start)` gives a Float span.
* **Net_Version** is `"CP2077CoopNet <major.minor.patch> proto <wire protocol>"`. `Net_Stats` JSON
  carries the same string as `"version"`.

* **Channels**: 1..15 are unreliable and sequenced. A snapshot older than one already delivered on
  the same channel is dropped. 16..31 are reliable and ordered, sharing one ordered stream per peer.
  Channel 0 is reserved.
* **Payload**: at most 1180 bytes, so one message fits in one datagram. Larger payloads make
  `Net_Send` return false.
* **Poll format**: `"<senderId>|<channel>|<payload>"`, oldest first. Transport events use sender 0
  and channel 0: `connecting <addr>`, `welcome <id>`, `peer_join <id>`,
  `peer_leave <id> left|replaced|relay_lost|bye`, `peer_unresponsive <id>`, `relay_lost`,
  `rejected <reason>`, `error <text>`, `disconnected`.
* **Send returns false** when the channel is invalid, the payload is too large, the transport is not
  connected, there are no peers, or the outbox (4096) is full.
* **Thread safety**: natives can be called from any thread. They only touch mutex-protected queues
  and atomics. The network thread owns the socket and all protocol state. Host resolution happens on
  that thread, so `Net_Connect` never blocks the game on DNS.

```lua
local CoopNet = require("coopnet")
CoopNet.connect("203.0.113.7", 11779, "kyle-and-friend")
registerForEvent("onUpdate", function()
    local handled, drainMs = CoopNet.pollTimed(function(sender, channel, payload)
        -- channel 0: transport events, 1..15 snapshots, 16..31 events
    end)
end)
CoopNet.send(CoopNet.EVENT, "weapon|draw|Items.Preset_Overture_Default")
```

## Wire protocol v1

Every datagram is one frame. The header is little-endian, 20 bytes:

| off | size | field |
|---|---|---|
| 0 | 4 | magic `CPN2` |
| 4 | 1 | protocol version (1) |
| 5 | 1 | channel |
| 6 | 2 | sender id (0 = relay) |
| 8 | 2 | target id (0 = relay, 0xFFFF = broadcast) |
| 10 | 2 | sequence (reliable: message seq; unreliable: per-channel seq) |
| 12 | 2 | ack: every reliable seq <= ack from the target was received |
| 14 | 4 | ack bits: bit i => seq ack+2+i received (selective ack) |
| 18 | 2 | payload size |

Control payloads (channel 0, first byte = op) are HELLO (nonce, room), WELCOME (id, nonce), PEERS
(id+nonce list), PING/PONG (id, timestamp), BYE, ACK and REJECT.

## Reliability

Reliability works end to end between the two game clients. The relay only forwards.

* Every frame to a peer piggybacks that peer's ack state. If nothing else is going out in a
  network tick, a pure ACK frame is sent.
* The resend timeout follows RFC 6298 from PING/PONG round trips every 500 ms, clamped between
  60 ms and 2 s. Ack-based samples are not used, because a cumulative ack that jumps after a hole is
  repaired would inflate the estimate.
* Fast retransmit needs three later messages acknowledged plus a RACK-style reordering window
  (SRTT x 1.25).
* The ack bitfield covers 32 sequences. A timer for a message beyond that horizon waits for the hole
  to be repaired instead of sending duplicates, and fires immediately once covered.
* Exponential backoff only applies while the peer sends no acks at all.
* After 64 transmissions of one message the peer is reported as `peer_unresponsive`.
* The window is 128 messages in flight. The receive buffer is 128 and the inbox holds 8192
  messages. When the inbox is full, reliable messages wait buffered and unreliable ones are dropped
  and counted.
* Membership comes only from the relay's PEERS list. A reconnecting client gets a new nonce, so
  both sides restart their streams together.

## Relay

```powershell
python tools\coopnet_relay.py --host 0.0.0.0 --port 11779              # real use, UDP port open
python tools\coopnet_relay.py --latency-ms 160 --jitter-ms 15 --loss-pct 2   # Russia<->LA bench
```

Jakub's Warsaw server only forwards `CP1,...` text packets, so this plugin needs this relay, or a
port of it, on a host that both players can reach.

## Tests (latest run)

* `coopnet_tests.exe`: 105 checks covering the codec, sequence wrap, RTT, SACK bits, backpressure,
  fast retransmit, the horizon and simulated links. Game-like events at 20/s with 2% loss and
  320 ms RTT have one-way latency p50 169 ms, p99 539 ms, max 889 ms. A 70,000-message transfer
  wraps the 16-bit sequence. Since 0.1.1 the run also covers Net_NowMs: FILETIME conversion against
  known dates, agreement with `system_clock`, no backward step over 200,000 calls, sub-ms values,
  call cost and elapsed time against `steady_clock` across a sleep. It also covers the Net_Version
  format and the startup summary line.
* `run_loopback.py`: two real `Transport` instances through the relay.
  * Clean link: 1000 + 500 reliable messages pass.
  * Transatlantic (160 ms each way, 15 ms jitter, 2% loss): 400 + 200 reliable messages arrive in
    order in 3.7 s, and RTT settles at 333 ms.
  * Hostile (20% loss with reordering): 300 + 150 in order.
* `test_relay_protocol.py`: 10 relay checks; it waits for the relay to come up and prints the relay's
  log if a check fails. `test_lua_helper.py`: 22 checks under LuaJIT 2.1.
* `verify_exports.py`: Main/Query/Supports exported, `Supports()` returns 1, no VC++ redist
  imports. The native list in `LoadReport.hpp`, the registrations in `Main.cpp` and the
  declarations in `Natives.reds` agree, and every name is in the image. The Net_Version string
  matches `CMakeLists.txt`, and the log marker occurs exactly once. `coopnet_plugin_probe.exe`:
  LoadLibrary + Query gives version 0.1.1, runtime 3.0.80.51928 and SDK 1.0.0.

## Known limits

* Not yet loaded in the game, so the Net_* RTTI registration and CET `Game.Net_*` lookup are
  unverified at runtime.
* No authentication or encryption. Anyone who knows the relay address and room can join.
* IPv4 only. One reliable stream per peer, so a lost event delays later events on every reliable
  channel. No fragmentation above 1180 bytes.
* The relay is a Python reference. Jakub's Warsaw relay cannot carry this protocol.
