# CP2077CoopNet phase 1 tooling

Tools for Phase 1 of the total sync plan ("prove the new plugin works in the game"):

- `lua/netprobe.lua`: **NetProbe**, a CET module. It sends probe traffic through the
  CP2077CoopNet plugin (`Game.Net_*`), measures loss, reliable ordering, one-way latency and
  `Net_Poll` cost, and writes the scoreboard audit log.
- `tools/sync_audit.py` scores two probe logs (host and joiner). It reports the real display
  error per direction and prints the Phase 1 exit verdict.
- `tests/` contains LuaJIT tests on a simulated link, synthetic-log tests for sync_audit, and a
  real-transport test through `coopnet_relay.py`. `run_tests.ps1` runs all of them.

Python 3.11+ is required. The tests also need `lupa` (see `requirements.txt`; `run_tests.ps1`
installs it into `.pydeps`). The native test needs VS 2022 (MSVC v143), CMake 3.21+, and the
preserved protocol-1 plugin history. In the unified repository `run_tests.ps1`
exports the pinned v0.1.2 commit `5826780c51317984c17a3d49c11a693e3d12f514`,
retained by the `plugin/` subtree import. It does not compile the current v2
transport with this legacy shim. Use root portable/native checks for current v2.

## NetProbe in the game

1. Copy `lua\netprobe.lua` next to the mod's `init.lua`. CET resolves `require` relative to the
   mod folder.
2. Merge these calls into `init.lua`'s **existing** handlers. CET retains one callback per
   event; registering a second `onInit` or `onUpdate` silently replaces v1 sync. NetProbe
   registers no events of its own. Declare the provider after the mod's `S` and `Sync` tables:

```lua
local NetProbe = require("netprobe")

local function readRemoteAvatarPose()
    if S.remoteHandle == nil or S.avatarParked then return nil end
    local ok, pos, forward = pcall(function()
        return S.remoteHandle:GetWorldPosition(), S.remoteHandle:GetWorldForward()
    end)
    if not ok or pos == nil or forward == nil then return nil end
    return pos.x, pos.y, pos.z, Sync.yawFromForward(forward.x, forward.y)
end

-- INSIDE the existing onInit, AFTER Diag.loadRole():
NetProbe.init({ role = IS_HOST and "host" or "joiner", drawnProvider = readRemoteAvatarPose })

-- INSIDE the existing onUpdate:
NetProbe.update(delta)

-- INSIDE the existing onShutdown (register it once only if the mod has none):
NetProbe.shutdown()
```

If you keep the file at `mods\<mod>\lua\netprobe.lua`, use `require("lua/netprobe")` instead.
Gate `NetProbe.init` behind a coop panel toggle (plan rule: new behaviour stays off until its
exit criteria pass). `NetProbe.setAudit(on)` toggles the AUDIT lines at runtime, and
`NetProbe.summary()` returns one line for the panel.

The provider above measures the visible on-foot avatar. Vehicle phases need a provider that
reads the actual displayed vehicle; do not substitute its incoming snapshot. For v2 supply
`renderDelayProvider = function() return actualRenderDelayMs end` from the renderer's chosen
timestamp. Do not substitute RTT or one-way latency. A v1 pose with no known render timestamp
is scored against present time and explicitly warned about.

If `Game.Net_Connect`, `Net_Send` or `Net_Poll` is missing (the plugin is not loaded),
`init` prints one line and returns false. After that every call is a no-op. If `Net_NowMs` is
missing, the probe still runs on a local fallback clock. It logs this once, and SESSION says
`clock=fallback`.

### Config (`NetProbe.init{...}`, defaults in `NetProbe.DEFAULTS`)

| key | default | meaning |
|---|---|---|
| `relayHost`, `relayPort`, `room` | `127.0.0.1`, `11779`, `""` | `room ~= ""` uses `Net_ConnectRoom` |
| `connect` | `true` | `false` reuses a connection someone else opened |
| `ownPoll` | `true` | `false` when init.lua drains `Net_Poll` itself; it must then pass each message to `NetProbe.handleMessage(sender, channel, payload)`, which returns true when the probe consumed it |
| `onOtherMessage` | nil | `function(sender, channel, payload)` for non-probe traffic and transport events while `ownPoll` |
| `role` | nil | `host`/`joiner`; nil reads `role.txt` (init.lua's role file), otherwise `local` |
| `unreliableChannel`, `reliableChannel` | `9`, `25` | probe channels (1..15 unreliable, 16..31 reliable) |
| `sendHz`, `reliableIntervalMs` | `30`, `1000` | probe rates |
| `audit`, `auditHz`, `auditDir`, `auditAppend` | `true`, `10`, `""`, `true` | AUDIT lines; `<auditDir>probe_audit_<role>.log`, all sessions preserved by default |
| `statsIntervalMs` | `1000` | STATS line rate |
| `maxPollPerFrame` | `64` | bound on `Net_Poll` calls per frame |
| `poseProvider` | player position + yaw from `GetWorldForward` | `function() -> x, y, z, yawDeg` (init.lua can pass its own, e.g. the vehicle pose) |
| `stateProvider` | speed buckets idle/walk/run/sprint/fast (0.3 / 3 / 5.75 / 10 m/s) | `function(horizontalSpeed) -> name` |
| `drawnProvider` | nil | `function() -> x, y, z, yawDeg` of the avatar this instance draws for the other player. With it, sync_audit also scores what is on screen (for example the v1 avatar) |
| `renderDelayProvider` | nil | `function() -> ms` actually used by the remote renderer; written as `rd=` per AUDIT line |
| `cpuClock` | `Net_NowMs / 1000` | precise poll-cost clock in seconds; `os.clock` fallback only when the native is absent |

All state lives in `NetProbe.s`. Every function closes over the `NetProbe` table only, which keeps
it far below LuaJIT's 60-upvalue limit (a test checks this).

### Wire messages

- Unreliable, 30 Hz: `NP1|u|<sid>|<seq>|<Net_NowMs>|<x>|<y>|<z>|<yaw>|<state>`
- Reliable, 1 Hz: `NP1|r|<sid>|<rseq>|<Net_NowMs>|<probes sent>|<reliable sent>`

`sid` is a per-session id. When a peer restarts, its counters are reset instead of showing up as
loss. A send the plugin refuses (no peer yet, full outbox) is counted as `u_fail`/`r_fail`, and
its sequence number is reused, so it never counts as network loss.

### Log format (`probe_audit_<role>.log`, one flushed line each)

```
SESSION t=<ms> probe=0.1.0 role=host sid=.. clock=Net_NowMs plugin=CP2077CoopNet_0.1.1_proto_1 relay=127.0.0.1:11779 room=default uch=9 rch=25 send_hz=30 audit_hz=10
EVENT   t=<ms> text=welcome 1 | peer_join 2 | probe_peer 2 sid=.. | reliable_gap ... | relay_lost ...
AUDIT   t=<ms> me=x,y,z,yaw spd=<m/s> st=<state> peer=2 rx=x,y,z,yaw rxs=<sent ms> rxr=<recv ms> rxq=<seq> rxst=<state> [drawn=x,y,z,yaw]
STATS   t=<ms> final=0|1 id peer frames u_sent u_fail r_sent r_fail u_rx u_exp u_lost loss_pct u_ooo u_last
        r_rx r_exp r_dup r_gap r_missing r_viol r_last peer_u_sent peer_r_sent lat_n lat_min lat_p50 lat_p95 lat_max lat_neg
        rlat_n rlat_p50 rlat_p95 rlat_max poll_frames poll_avg_ms poll_max_ms poll_slow poll_msgs poll_max_msgs poll_capped
        audit_lines errors
```

Every `t`, `rxs` and `rxr` comes from `Game.Net_NowMs()`. Both bench instances run on one PC, so
the two logs share a clock. Loss is counted between the first and the last received sequence
number. Latency percentiles come from a 1 ms histogram and are reported as bin centres.
Poll timing uses `Net_NowMs` by default. `poll_p99_ms` uses microsecond bins; the highest bin
reports the measured maximum above 5 ms. `os.clock` fallback remains coarse and is unsuitable
for sub-millisecond worst-case claims. `peer_sid`, `peer_resets`, `u_first`, `r_first` and
`rx_last_ms` preserve epoch/freshness information in STATS. Before a peer resets or leaves,
the previous epoch's STATS are flushed. Reload appends a SESSION and flushes the old one.

With `ownPoll=false`, the single FIFO owner must time its existing drain and call
`NetProbe.recordDrain(costMs, messageCount, capped)` once per frame, including empty drains.
Use `NetProbe.clockSeconds()` around the drain to use the same clock. The scorer warns and
refuses a complete pass when an external drain's cost was never measured.

## sync_audit.py

```powershell
python tools\sync_audit.py <Baseline mod>\probe_audit_host.log <Test B mod>\probe_audit_joiner.log --sim-loss-pct 1
python tools\sync_audit.py host.log joiner.log --min-duration-s 1800 --json report.json --strict
```

The two directions are "joiner sees host" and "host sees joiner". For every AUDIT line of the
viewer at time `t`, the tool reports:

- **snapshot error (rx)**: `|received pose - subject's true pose at (t - delay)|`. This is not
  on-screen error. The subject's
  true track is interpolated linearly between its 10 Hz `me=` samples (it is not interpolated
  across gaps larger than `--max-gap-ms`). The delay is `--delay-ms` if given, otherwise the
  measured median one-way latency of that direction.
- **vs true pose now**: the same, with delay 0.
- **display error (drawn)**: the actual drawn pose against `true(t - rd)` for that AUDIT line.
  `--delay-ms` explicitly overrides both directions. With no `rd` or override, use present time
  and warn; never silently substitute median latency for the renderer's timestamp.
- **integrity**: `|received pose - subject's true pose at its send time|`. This should be about
  0. The floor of the 10 Hz interpolation is about 3 cm p95 at 14 m/s on an 8 m circle, and up
  to about 0.3 m when the speed changes inside one sample interval. A large value means the
  clocks or the logs do not match.
- **yaw error**, **staleness** (`t - sent`), and **latency**. All of these are given as
  p50/p90/p95/max, and also per movement state of the subject (`st=`), including present-time
  `rx_now` and `drawn_now`. Probes older than 2 s are excluded from pose scoring and counted.
  Missing drawn samples produce an explicit warning, not a claim of visual alignment.

Loss, reliable and poll numbers come from each probe's final STATS line. The verdict checks:

1. Probe loss is about the simulated loss: `|measured - sim| <= max(--loss-tol-pct, 4 sigma
   binomial)`. The floor defaults to 0.25 percentage points. For comparison, v1 missed 16.5%.
2. Poll cost is below `--poll-limit-ms` (0.1) per frame on both sides.
3. The reliable channel has no dup/gap violations, and no unreliable message arrives out of
   order.
4. Delivery covers the accepted stream: at most 3 reliable messages and 2 seconds of unreliable
   sends are missing at either end; received probes are never observed more than 2 s old.
   A stalled channel that recovers at the end still fails the uninterrupted-soak criterion.
5. Both logs have final STATS and no peer resets. Multiple SESSION blocks fail by default;
   `--session INDEX` explicitly scores a selected segment, never a whole interrupted soak.
   Earlier epoch/session stats and events remain in `session_history` in JSON.
6. Both AUDIT tracks exist, have no gaps above 2 s, and their observed overlap meets
   `--min-duration-s` (use 1800 for 30 minutes; run slightly longer to allow startup and the
   final sampling interval). Distant SESSION/final STATS timestamps alone do not prove duration.

The small reliable tail allowance is for messages still in flight at shutdown. PASS does not
assert zero undelivered reliable messages; inspect the reported tail or drain it to zero.

Exit code 0 means the report was printed. With `--strict`, a failed verdict exits 1. Unusable
input exits 2.

## Historical Phase 1 bench procedure

This protocol-1 procedure is retained for interpreting old captures. Do not deploy
it over the current v2 gameplay plugin or enable a second native polling owner.
The consolidated test runner exports its pinned relay under `build/coresrc/`.

1. With the games closed, install the plugin and NetProbe into Baseline and Test B.
2. Run `python build\coresrc\tools\coopnet_relay.py --port 11779 --latency-ms 115 --jitter-ms 20 --loss-pct 1` after exporting the pinned core with the test runner.
3. Play or run the bot for 30 min while v1 sync keeps running.
4. Shut the probes down cleanly and run `python tools\sync_audit.py <Baseline>\...\probe_audit_host.log <Test B>\...\probe_audit_joiner.log --sim-loss-pct 1 --min-duration-s 1800 --strict`.
5. Archive both files before another bench. Review warnings, drawn sample coverage, duration
   and earlier sessions. A transport PASS does not establish vehicle/NPC/world synchronization.

## Tests

```powershell
powershell -ExecutionPolicy Bypass -File run_tests.ps1               # everything (about 80 s)
powershell -ExecutionPolicy Bypass -File run_tests.ps1 -SkipNative   # no compiler needed
```

- `tests\test_sync_audit.py` (31 tests) writes synthetic logs with closed-form errors and checks
  the following against them: delay alignment, percentiles (also cross-checked with numpy when
  it is installed), per-state grouping, drawn pose, yaw wrap, track gaps, session selection,
  verdict rules and CLI exit codes.
- `tests\test_netprobe_sim.py` (33 tests) runs two `netprobe.lua` instances under LuaJIT 2.1
  (lupa), loaded with `require("netprobe")` against a mocked CET `Game`. They talk over an
  in-memory link on a virtual clock: 300 s at 115 ms + 20 ms jitter with 1% loss, and 120 s with
  5% loss. The tests check:
  - The probe's loss equals exactly the number of probes the link dropped.
  - The reliable channel stays clean, and injected dup/drop/reorder faults are caught.
  - Rates, latency, and poll bounding and cost are right.
  - The no-plugin path logs once and does nothing else.
  - The upvalue limit holds.
  - sync_audit scores the real audit files. That includes a "perfect avatar" oracle, which must
    score about 0 at its own render delay.
- `tests\test_netprobe_relay.py` (14 tests) needs a native build:
  - The script exports `src\core` from the preserved original plugin commit
    `5826780c51317984c17a3d49c11a693e3d12f514` in the parent repository and builds
    it into `build\shim\Release\coopnet_shim.dll`. The shim is test-only and is never shipped.
  - Two probes reach the real `coopnet::Transport` through LuaJIT FFI. They talk real UDP in
    real time through `coopnet_relay.py`. There are two 30 s profiles: 115 ms + 20 ms jitter with
    1% loss, and 60 ms + 10 ms jitter with 5% loss. The sync_audit reports land in
    `out\relay\<profile>\report.txt`.

## Known limits

- `Net_Poll` is a single FIFO. Only one consumer may drain it. When init.lua drains it, use
  `ownPoll = false` plus `handleMessage`.
- Only the lowest-id peer that sends probes appears in AUDIT lines, which is enough for the
  two-player bench.
- `os.clock` fallback has 1 ms resolution on Windows; the normal clock is `Net_NowMs`.
- The tests cannot measure the in-game cost of a CET to RTTI native call. The shim test only
  bounds the transport side. The real number comes from the bench STATS.
