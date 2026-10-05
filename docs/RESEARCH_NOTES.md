# Primary-source research and next engine experiments

Checked 2026-10-04. This note supports [MULTIPLAYER_PLAN.md](MULTIPLAYER_PLAN.md). No third-party implementation code was copied, no dependencies upgraded, and no live game experiment was run for this research. Source declarations and another project's code are evidence of possible mechanisms, not proof of this mod's runtime behavior.

## Reproducible source checkpoints

| Source | Pinned checkpoint | What was actually inspected |
|---|---|---|
| Local reconstructed Cyberpunk scripts | `a2e6bb31298fc7c613e5bfa3d22d424cdeca3f9a`, 2.31 corpus | Mounting declarations, vehicle callbacks, quest/stat/inventory declarations |
| Installed Codeware implementation reference | `b1b2770cdf6ad2631666fb6ef4ccda99d864298e`, installed version 1.18.0 | Asynchronous dynamic entity lifecycle; previous v36 cleanup research |
| GWheel | `4b2e9be4caa4f7f21ba59d954fbb617bc1ffe21c` | Local-player filtering around completed mount and beginning unmount callbacks |
| Cyberverse | `645d17809f0262586ff8687f1896984e821b5e6f` | MIT license, README, redscript entity/teleport bridge, player action tracker and interpolation data |
| Friend CP2077-Coop | `20eb125796a4a5e55394cc2c3ed344f9b8456b4a` | Separate review of build/tests/session policy; root independently ran three portable CTest suites |

Local corpus path: `C:\Users\User\AppData\Local\Temp\claude\G--SteamLibrary-steamapps-common-Cyberpunk-2077\178a0b72-ad30-4d7d-9e67-874b3f897893\scratchpad\decomp_231`. It is a reconstructed source reference. Compile against the installed game cache and observe behavior before adding runtime assumptions. The online Codeberg pinned view was unavailable during this check; the exact local revision supplies the API evidence below.

The installed Codeware baseline remains 1.18.0. A newer release being available is not by itself a reason to change the tested runtime.

## Vehicle mounting: exact checks and asynchronous state

In `core/components/scriptComponents/vehicleComponent.swift`:

- `IsDriverSlot`, around line 202, checks whether the slot name **contains** `seat_front_left`; it is not strict equality. For one known test car, use an exact allowlist of supported slot names in the network policy. Do not accept arbitrary strings because the game helper classifies them as driver-like.
- `GetDriverSlotID` and passenger helpers identify `seat_front_left`, `seat_front_right`, `seat_back_left` and `seat_back_right`. These names do not prove every model has those seats. `IsSlotAvailable`, line 864, checks slot existence and occupancy.
- `IsDriver`, line 296, checks a valid mount parent, driver slot, resolved vehicle and excludes `AVObject`. `IN_VEHICLE` and a model index cannot establish driver authority.
- `GetDriver`, line 423, has a remote-control branch. Restrict the first experiment to an ordinarily mounted base-game car and separately test remote-control behavior later.
- `OnMountingEvent`, line 1396, and `OnUnmountingEvent`, line 1490, handle transition side effects. `OnVehicleFinishedMountingEvent`, line 1573, distinguishes mounting from unmounting with `isMounting`, and receives a character and slot. `OnVehicleStartedMountingEvent` can schedule a delayed finished event. Events describe lifecycle stages; do not assume a request call completes the animation immediately.

`orphans.swift:22318` declares `IMountingFacility` queries plus `Mount` and `Unmount`, both returning **Void**. `MountingInfo` contains child, parent and slot. `MountingRequest` at line 37105 includes this relationship and mount data; `UnmountingRequest` at line 35890 also includes a delay. `VehicleFinishedMountingEvent` at line 36834 carries `slotID`, `isMounting` and `character`.

The game's seat-switch path in `cyberpunk/player/psm/vehicleTransition.swift:1456` builds a request with explicit parent/child/slot and instant/silent-unmount options. This is an example from an existing state-machine transition, not evidence that injecting it into an arbitrary player state is safe. Decompiled optional arguments can be visually ambiguous; validate the chosen query/request signature with the compiler instead of guessing positional omissions.

The independent [GWheel mount wrappers](https://github.com/clevergrant/cp2077-wheel-mod/blob/4b2e9be4caa4f7f21ba59d954fbb617bc1ffe21c/gwheel_reds/gwheel_mount.reds) filter callback characters to the local player, enable their input path at finished mounting, and disable it at unmount start. This supports observing both boundaries. Our seat registry still needs actual relationship checks and its own request/epoch validation; the wrapper is not a network authority implementation.

### Stationary seat-observation experiment

**Question:** when does the engine's actual mounted relationship agree with the requested seat, and which callbacks/query observations safely confirm entry and exit for the chosen car?

**Scope:** one explicitly owned, nonpersistent parked base-game car, a local player and optional already-owned test avatar. First run observes ordinary manual enter/exit; no remote driving, vehicle physics flags, arbitrary vehicle adoption or population suppression. Do not delete the car with any local player mounted.

**Preparation:** add an off-by-default observer in a separately reviewed source change. Compile it against 2.31; keep the callback path scoped to the test car and local player. The observation loop reads at a bounded cadence (proposed 10 Hz), and callbacks append small bounded records. This research note has not implemented or compiled that observer.

**Record:** monotonic timestamp; local session/generation; owned network identity and local EntityID; requested operation/request ID if present; event kind and character; queried child/parent/exact slot; `IsDriver`; seat availability; vehicle/player attachment state; player state-machine transition when available. Record actual values, not only an `accepted=true` return. Include timeout and late callback records.

**Sequence:** ten normal driver enter/exit cycles; ten supported passenger cycles; interrupted entry; entry followed by immediate exit; a requested seat that is already occupied; a seat switch if supported; and session/reset during a pending transition. Begin with manual actions so the log characterizes the game before an injected mount mechanism is introduced. Repeat any game-mutating cases only in the authorized backed-up bench.

**Expected observations to test, not assumed facts:** mount confirmation requires an attached player with the expected child/parent/exact slot and a compatible finished state. Exit completion requires absence of that relationship and a safe on-foot player, not merely the beginning-unmount event. If events are missed, a bounded query-based reconciliation may recover the state, but must not invent animation completion. If a timed-out entry later completes, quarantine/reconcile the old reservation instead of granting the seat to someone else immediately.

**Gate:** every cycle has a complete observation trace; no two network reservations become confirmed for the same seat; queried relationships agree with the accepted registry; failed/late transitions produce a bounded explicit outcome; removal occurs only after observed dismount and engine disappearance. Report timing distributions. This would justify the next stationary seat coordinator; it would not prove moving-passenger stability, remote input simulation or shared collision physics.

## Entity creation and deletion remain asynchronous

The pinned [Codeware DynamicEntitySystem implementation](https://github.com/psiberx/cp2077-codeware/blob/b1b2770cdf6ad2631666fb6ef4ccda99d864298e/src/App/World/DynamicEntitySystem.cpp) separates managed registration, asynchronous stub creation and population attachment/removal. `DeleteTagged` drops tag lookup state before engine removal, and deleting while stub creation is pending can race its later callback. Therefore a missing tag is not a deletion acknowledgment.

The v36/v37 harness tracks the created local ID, pending/spawning state and retained retiring attachment. Preserve that behavior when applying the same ownership discipline to cars. A dynamic entity handle or `IsAttached=true` alone also failed to prove correct initial placement in the v35 bench: actual pose remained at origin. Check the measured spawn position before announcing readiness. These are observed local failures and pinned implementation findings, not hypothetical concerns.

## Other multiplayer projects: useful evidence and limits

### Cyberverse

[Cyberverse at 645d1780](https://github.com/TDUniverse/Cyberverse/tree/645d17809f0262586ff8687f1896984e821b5e6f) carries the [MIT license](https://github.com/TDUniverse/Cyberverse/blob/645d17809f0262586ff8687f1896984e821b5e6f/LICENSE). Its README describes a developer framework targeting game 2.1 and explicitly notes missing pieces; that is not 2.31 compatibility or acceptance evidence.

The [redscript network bridge](https://github.com/TDUniverse/Cyberverse/blob/645d17809f0262586ff8687f1896984e821b5e6f/client/RedscriptModule/src/Network/NetworkGameSystem.reds) uses Codeware transient entities and `AITeleportCommand` with navigation testing disabled for puppets. That independently corroborates a mechanism already established by our v37 live experiment. Its additional collider/force-tick choices require separate justification; they are not adopted here.

The [player action tracker](https://github.com/TDUniverse/Cyberverse/blob/645d17809f0262586ff8687f1896984e821b5e6f/client/red4ext/src/PlayerActionTracker.cpp) observes vehicle mount/unmount and equipment events, but explicitly leaves movement animation handling aside; its vehicle unmount path includes an identity TODO. The [interpolation data](https://github.com/TDUniverse/Cyberverse/blob/645d17809f0262586ff8687f1896984e821b5e6f/client/red4ext/src/PlayerSync/InterpolationData.h) represents a simple elapsed-time segment. These files do not establish a solution to our long-sprint/animation or seat-authority problems. We have neither built nor run this framework.

### Tilted Phoques CyberpunkMP

The inspected repository checkpoint was `0ccb0cfa78222cc463ea937e0c8271060f9afd2a`. Its [custom license](https://github.com/tiltedphoques/CyberpunkMP/blob/0ccb0cfa78222cc463ea937e0c8271060f9afd2a/LICENSE.md) expressly restricts use and analysis for competing products. The license was retrieved alongside initial source files; after discovering that restriction we excluded its implementation from adaptation and further mechanism research for this project. No source was copied or incorporated. Its README's feature claims are not our benchmark evidence. Continue independent API research or seek appropriate permission before considering reuse.

### Existing transports

[Valve GameNetworkingSockets](https://github.com/ValveSoftware/GameNetworkingSockets) is a primary example of a transport with reliable/unreliable delivery, encryption and network simulation. Its documentation explicitly separates transport from higher-level entity serialization. It also distinguishes the open-source library from Steam authentication/signaling/relay services. It is a research option if a measured transport/security gap warrants evaluation, not a chosen replacement for the working CB77 path or a shortcut to gameplay authority.

## Combat, inventory and quests: API availability is only a starting point

The local corpus declares `QuestsSystem.GetFactStr/SetFactStr` (`orphans.swift:17075`), transaction `GiveItem/RemoveItem` (around 18031), hit events (around 18681) and stat-pool changes (around 20575). Those local operations do not supply distributed exactly-once rewards, correct damage validation, coherent scene transitions or multiplayer save semantics.

Each supported domain therefore needs a host policy, stable identity, idempotent event/transaction, observed engine result and restart test. Quest work begins with a small explicit compatibility matrix and disposable saves. Appearance and animation require their own actual visual checks and supported asset/version list. The available sources do not justify promising generic quests, arbitrary traffic or identical physics.

## Verified source-integration evidence

Root verified the default gameplay suite, including redscript sandbox compilation, completed with zero failed groups in 41 seconds and the explicit known A10 assertion waiver (`D:\Downloads\syncfix\bench-artifacts\20261004-integration\gameplay-full.log`). The consolidated phase1 runner passed 31 scoreboard, 33 LuaJIT and 14 actual UDP tests; its native profiles took 61 seconds (`phase1-consolidated.log` in the same artifact directory). The shim uses the retained pinned legacy revision `5826780`, not the incompatible current v2 transport source. These are offline checks; no games were launched or redeployed.

Root independently verified **115/115 relay tests** (4.875 s, `relay-authority-full.log`) and the overlapping **21/21 focused authority tests under `python -O`** (1.713 s, `authority-optimized.log`), including real UDP bootstrap/loss/reconnect cases. Accepted duplicates return current state without reapplying a mutation; the implementation does not retain every original result. A disconnected seat is released as metadata, not by observing engine dismount. The game adapter does not yet expose the authority route. See [the exact experiment contract](../relay/docs/AUTHORITY_EXPERIMENT.md).

The imported NPC harness also passed **48/48 tests** using the main pinned test dependencies; `npcsync-consolidated-clean.log` uses the documented `-p no:faulthandler` setting for Windows LuaJIT's handled SEH noise, without changing assertions. Build/test corrections at `b4c328f` add the explicit math include, exact-byte comparison, Python >=3.12, and bounded native test pacing while retaining >100 samples and the 0.05 m accuracy threshold. Consolidation/path and shell-LF fixes are at `75cf9c9`.

Authority source was committed as **90d5143**; verified integration/test fixes are pushed through **856721b**. The integrated repository's [CI run 37259471154](https://github.com/KyleBuildsAI/cp2077-coop/actions/runs/37259471154) completed successfully on that source: **all four jobs pass**. Windows native passed **15 suites in 80.97 s**, including DLL load and clean/bench/lossy/restart loopbacks; Linux portable and Linux ASan/UBSan each passed **nine suites**, including the 115-test relay suite. Windows runtime mocks passed with an explicit redscript SKIP because the game is absent and the known A10 waiver. Local default gameplay checks above supply the separate redscript compilation evidence. CI evidence was archived under `build/ci-evidence/37259471154` (71 files, 344,625 bytes); the shared bench artifact root preserves CI and local check/loopback logs.

The preceding [run 37259233550](https://github.com/KyleBuildsAI/cp2077-coop/actions/runs/37259233550) passed all 15 Windows native and nine Linux portable suites but failed the sanitizer Debug microbenchmark's 20-microsecond timing budget at 53.734 microseconds. That negative diagnostic is preserved under `20261004-integration/ci`, alongside local checks/loopbacks in sibling artifact folders.

Commit **856721b** explicitly skips only the Release timing-budget assertion when instrumented, while still running the benchmark and all behavior checks. A local Release interpolation run passed 42 checks at 728 ns/sample. This is test-machine timing, not an in-game frame cost or a sprint-quality result. It is distinct from the still-open A10 gameplay assertion waiver. Keep actual CI results separate from the friend's earlier CI and from configured jobs. No gameplay stage advances solely because these offline tests or source examples pass; a later deployment needs its own manifest/live result. See [integration provenance](INTEGRATION_PROVENANCE.md) for the import map.

## Contributor checkpoint narrative comparison

See [Bukczyk checkpoint comparison](BUKCZYK_CHECKPOINT_COMPARISON.md) for the 2026-10-04 user-supplied update, current source/documentation mismatches and pending session, scaling, rate, bootstrap and cutover experiments. GitHub main remains the previously inspected 20eb125 checkpoint; older passing CI at 6d6ac003 is kept separate from its later networking work-in-progress. No new runtime validation or implementation adoption occurred.

## Public multiplayer platform research

The [2026-10-04 landscape review](MULTIPLAYER_LANDSCAPE_2026-10-04.md) records OPEN//77, CyberMP and other projects, pinned public sources, reuse boundaries and an independent research queue. Public APIs are not our engine APIs. No competitor implementation was integrated. [Claude handoff](CLAUDE_HANDOFF.md) records documentation-only scope and machine/package state.
