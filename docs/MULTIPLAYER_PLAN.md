# Multiplayer implementation and acceptance plan

Updated 2026-10-04. This is the canonical shared roadmap for Kyle, Jakub, Claude Code and Codex. The Obsidian vault links here; it is not a second implementation plan. This document describes intended work, dependencies and observable gates, without delivery estimates. A stage is complete only when its stated evidence exists.

## Starting point and scope

The latest game-tested baseline is mod **v0.0.37 / af1f98f** with **CP2077CoopNet 0.2.0-alpha.5 / 1f270f1**, installed Codeware **1.18.0**, and the local **2.31** script corpus. The final v37 run demonstrated one explicit test NPC moving on both peers and removal, map-pause/resume, quickload and peer-departure cleanup. It did not demonstrate shared autonomous AI, combat or traffic. The player marker was observed in-world, on the full map and nearby minimap; RTT is shown in the panel. Player locomotion still has delay and frequent corrections. Existing vehicles are cosmetic replicas without persistent shared identity or seat authority.

That run lasted 600.07 seconds and deliberately included interruptions. It was not an uninterrupted soak or an internet test. The offline baseline had zero failed test groups **with the known A10 long-sprint assertion waiver**. Neither that result nor the later repository work closes the movement-quality gate. The optional NPC and retained-player-command experiments are off in the restored game configurations. See the dated live evidence in `tests/STEERING_TRACE.md` and the Obsidian note `Live Test 2026-10-04 (Codex takeover)`.

The history-preserving consolidation checkpoint is **61ea57c**: `plugin/`, `relay/`, `npcsync/`, `phase1/` and `phase1-check/` are now under this repository. The old standalone checkouts are references, not parallel development roots. Verified source checkpoint **856721b** passed all four jobs in [CI run 37259471154](https://github.com/KyleBuildsAI/cp2077-coop/actions/runs/37259471154): Windows native, Linux portable, Linux ASan/UBSan and Windows runtime mocks. Local gameplay/redscript checks also pass with the known A10 waiver. The authority implementation has passed headless real-wire tests, while its game bridge and in-game seat validation remain future work. These source results do not imply a new game deployment.

The goal is a coherent two-player co-op experience across players, owned vehicles, supported NPCs, combat and shared world progress. Two release scopes prevent a small demonstration from being called complete multiplayer:

- **Playable co-op sandbox:** reliable two-PC sessions; recognizable and acceptably smooth players; shared rides; a bounded shared encounter; consistent health/death; documented streaming and reconnect behavior.
- **Campaign-capable co-op:** the above plus individually verified quest/scene, inventory/reward and persistence adapters across a published compatibility matrix. A generic fact copier or one working quest does not establish general campaign support.

Unsupported cases must be explicit and fail safely. The plan does not declare general campaign support impossible; it requires evidence for each engine-dependent adapter.

## Authority and integration rules

The host owns world identities and accepted gameplay outcomes. The relay binds requests to admitted peers, enforces roles, bounds queues and rejects stale generations. A joiner sends intent for seats, damage and shared interactions. A transport cookie, packet checksum or sender-supplied peer ID is not equivalent to authenticated gameplay authority.

Every replicated object needs a network identity independent of its local `EntityID`, with a session/world epoch and incarnation or non-reused lifetime ID. Every mutation needs a revision or request/event identity. Reliable retries must not duplicate an effect; old state must not resurrect a removed object. A snapshot and its subsequent deltas must share a revision boundary.

Keep the current **CB77** transport and its clock/reliability tests. The friend's portable policy is useful input to ownership, admission and lifecycle design; its incompatible wire format is not a drop-in replacement for the deployed plugin. The optional **C3A1** script authority route uses reliable **channel 21** and pose **channel 3**. It is a tested headless policy experiment, not a vehicle game bridge. The main game adapter does not yet expose its relay-source messages or these channels. NPC test channels remain 20/2. Give each domain scoped queues and generations, or one deliberate dispatcher; preserve exactly one `Net_Poll` owner.

The current bounded replay policy replies to accepted duplicates with a duplicate/already-accepted result and current state; it does not retain every original result. It never reapplies an accepted mutation. Stronger original-result/transaction recovery requirements below are future domain work, especially for combat and inventory. Similarly, removing a disconnected peer's seat lease is metadata cleanup, not proof of engine dismount. The exact implemented contract is in [AUTHORITY_EXPERIMENT.md](../relay/docs/AUTHORITY_EXPERIMENT.md).

Apply game changes on the game thread through bounded work queues. An accepted API call is a request, not proof of an engine result. Observe actual spawn placement, mounting, damage, deletion and world-load completion before acknowledging them. The v35/v36 NPC failures are concrete examples of this distinction.

Separate host authority over identity/outcomes from the machine simulating physics. The first vehicle is host-driven. Any later joiner driving requires an explicit choice between host-side remote-input simulation and a revocable driver simulation lease. Either choice keeps host seat arbitration, stale-owner rejection and a defined correction policy.

## Stage map

| Stage | Deliverable | Depends on | Current evidence |
|---|---|---|---|
| M0 | One repository, reproducible packages and trustworthy tests | Existing baseline | Histories consolidated; all four source CI jobs/local regressions pass, A10 remains; new gameplay release/rollback gate is separate |
| M1 | Admitted sessions, world epochs and authoritative object policy | M0 | Existing v2 transport live; new policy passes headless tests, no game bridge |
| M2 | Player motion, appearance, equipment and marker fidelity | M0, existing v2 | Players/marker live; quality and complete appearance remain open |
| M3 | One owned vehicle, persistent parking and confirmed seats | M1, game API probe | Research only; no authoritative shared car in game |
| M4 | Shared driving, passengers and controlled driver transfer | M2, M3 | Pending engine/physics experiments |
| M5 | Bounded NPC encounter with identity and lifecycle | M1, M2 | One controlled pose-mirrored actor observed; autonomous encounter pending |
| M6 | Host-accepted combat, health, death and respawn | M1, M5; M4 for vehicle damage | Pending authoritative integration |
| M7 | Streaming, late join and reconnect across all implemented domains | M1 onward | Selected single-actor restart/cleanup observed; general recovery pending |
| M8 | Supported world interactions and transactional inventory | M1, M6, M7 | Pending object/transaction adapters |
| M9 | Explicit quest, dialogue, scene and save compatibility | M7, M8 | Research and isolated adapters required |
| M10 | Two-PC hardening, performance and release qualification | Every claimed domain | Local simulated-link evidence only |

M2 and the M3 API probe can progress in parallel. M7 is a requirement of each domain from its first implementation, not cleanup postponed until the end. Security begins at M1 and is tested throughout.

## M0 — reproducible shared development

Keep one canonical source tree and matched deployment manifest for Lua, redscript, DLLs and protocol version. Work in small feature branches with ownership declared before overlapping changes. Import external work with its commit, license/provenance and dependency pins; preserve authorship. Do not overwrite the newer runtime with an older imported game bridge.

Required checks are portable plugin/protocol tests on Windows and Linux, sanitizer runs where supported, relay/authority tests, Lua runtime mocks, and installation-specific redscript compilation. A CI machine without the game must report the redscript check as **SKIPPED**, not passed. Preserve the A10 waiver visibly until a replacement test and live trace establish the intended behavior. A file-hash check certifies package identity, not runtime functionality.

**Gate:** a fresh clone builds and runs its supported tests; a package manifest identifies all required files; an isolated deployment/rollback reproduces the tested version. Test reports distinguish PASS, FAIL and SKIP. The optional policy route is disabled by default and legacy/current traffic remains unchanged when it is off.

## M1 — sessions and object authority

Bind the admitted connection to a role and peer identity. Allocate world epochs and object IDs on the authority side. Reject unauthorized create/remove/state messages, invalid or non-finite values, unknown record/seat choices, excessive rates, old epochs and ownership violations. Bound per-peer requests, entities, replay caches and pending work. For future effectful transactions, retain the original result within a documented replay window so a duplicate can recover it without executing again; outside that window require reconciliation. **This stronger transaction gate is not implemented by the current C3A1 experiment**, which returns duplicate/already-accepted plus latest STATE and never reapplies. Its record label is also inert metadata; game record/seat allowlisting belongs to the later bridge.

Implement the initial seat/object policy over the current reliable route with a real wire harness. Admission/authentication and endpoint binding must be exercised at the actual relay boundary; calling a trusted registry directly is insufficient. Specify behavior when the host leaves: close/freeze the session and clear owned replicas initially. Host migration is a separate future feature.

**Gate:** two clients through the actual relay demonstrate admitted identity binding, session isolation, old-epoch rejection, simultaneous conflicting requests, deduplication, bounded memory, reconnect and host-loss cleanup. Malformed/flooded input cannot grant ownership, corrupt another room or starve player pose traffic. Headless passing remains distinct from in-game mounting or movement.

## M2 — players and recognizable remote avatars

Measure the actual remote entity, raw snapshots and the intended render sample on a common clock. Maintain one interpolation/prediction owner. Test same-build, same-route, synchronized inputs before comparing steering approaches; include continuous **12-second sprint** segments, turns, stops, crouch, jump and vertical transitions. Record every teleport request separately from hard-correction counters, with generation boundaries and delayed execution accounted for.

Initial movement targets retained from the earlier plan are **walk/run position p95 <= 0.5 m** and **sprint p95 <= 1.5 m** against the declared delayed render timeline. Report present-time lag separately, in both directions. These are unmet acceptance targets, not current measured performance or a promised universal latency. Keep `native_retarget=false` unless a matched experiment improves the measured result.

Replicate validated appearance/equipment data separately from movement. Start with supported base-game bodies, clothing and weapon presentation. Establish a safe fallback for missing mod assets and a bounded appearance payload. Test pose/action transitions rather than assuming a synchronized position implies correct running, shooting, reloading or seated animation. Native locomotion/animation experiments need verified 2.31 interfaces, shutdown cleanup and an opt-in fallback.

**Gate:** movement targets on the chosen latency/loss profiles, documented correction rate and frame cost, recognizable supported appearances on both peers, equipment changes without phantom items, and marker position/staleness across both roles, vehicle use, streaming, reload and disconnect. RTT must not be labeled visual delay. See [research notes](RESEARCH_NOTES.md) for the limits of other projects' animation evidence.

## M3 — one owned parked car and confirmed seats

Start with one explicitly spawned base-game car, persistence off, host-owned registry and full transform. Keep it parked after the driver exits. Model identity, record, appearance, pose and seat occupancy independently of the current driver. A record/model index is not an entity ID.

Use request -> reservation -> actual mount -> confirmation. Whitelist the exact slots supported by that record. A mount request returns no completion proof. Observe the actual child, vehicle parent and slot plus the relevant finished event. On timeout, stale generation or interrupted animation, reconcile what the engine actually did before issuing another grant. Late completion of an expired request must not create a second occupant. Release a seat only after actual dismount; never delete a car while the local player remains mounted.

**First experiment:** the bounded stationary observer in [research notes](RESEARCH_NOTES.md#stationary-seat-observation-experiment). It tests the mount lifecycle before injecting remote controls.

**Gate:** repeated host enter/exit/re-enter preserves the same network car and parked pose; competing driver requests produce one grant; exact parent/slot agrees with the registry; failed/late mount attempts cannot leak seats; removal waits for empty seats and actual entity disappearance. Then confirm joiner passenger mounting while stationary. No moving-passenger claim yet.

## M4 — shared driving and passengers

Expose and retain vehicle identity, full quaternion, linear/angular velocity and freshness through the native/game boundary. Existing codec fields are insufficient if dispatch drops them or the script ABI cannot access them. Determine experimentally how to keep replica physics from fighting received transforms without affecting ambient cars. Do not apply an unverified chassis flag globally.

First test host driving with a stationary-established passenger, then slopes, turns, reversing, braking and exit while stopped. Add driver handoff only with an authority generation, explicit revoke/grant, input/state ownership checks and an observed transition. Vehicle collision and damage outcomes belong to M6; matching transforms does not establish identical collision simulation.

**Gate:** on a recorded route up to 30 m/s, car-origin position p95 <= 0.5 m and orientation error p95 <= 2 degrees against the chosen render timeline; report interpolation delay, peak errors and corrections. Passenger remains attached to the granted seat, both peers agree on driver/occupancy, old driver updates are rejected after handoff, and a disconnected occupant cannot trap the other player. Repeat under loss/reorder before claiming a shared ride.

## M5 — a bounded shared NPC encounter

Build from the optional single owned actor. Add a deliberately bounded pool with host identities, incarnations, appearance, actual state and acknowledged cleanup. Start with test actors in a controlled encounter area, then one verified class of ambient actor. Host AI decides targets/behavior; joiner replicas display accepted outcomes. Position mirroring alone does not synchronize navigation, animation, perception, combat or ragdoll.

Maintain one authoritative actor per supported network identity. Do not infer that vanilla `EntityID`s match across saves or suppress the entire joiner population. Any ambient/quest adoption needs an independently verified stable world identifier and a scoped replacement policy. Preserve pending creation and retiring handles until real engine completion, as the v36 cleanup fix does.

**Gate:** the configured actor cap holds during spawn/remove/rejoin bursts; duplicate, reordered and stale messages produce no duplicate/origin actors; actors enter/leave interest regions predictably; both clients identify the same supported actor and observe agreed movement/action/death transitions. Cleanup is checked by saved local ID/engine lifetime, not just disappearing tags. Traffic remains a separate expansion after NPC/vehicle authority is proven together.

## M6 — combat, health, death and respawn

Give every hit request a unique identity and an admitted attacker. The host validates the supported weapon/action, target identity, range/timing and rate, computes the accepted health/state result, and emits one result revision. Do not trust a joiner's proposed damage amount. Define lag compensation explicitly against retained host history; a ray against current positions is not validation against the player's delayed view.

Prevent locally predicted replica damage from applying again when the authoritative result arrives. Hook the engine's actual damage/death paths carefully; writing a health pool alone may omit armor, status effects, reactions, rewards or quest callbacks. Start with one ranged weapon and one controlled NPC; then melee, armor/status effects, explosives and vehicle damage as separate tested classes. Decide friendly fire explicitly.

Player death needs a coherent co-op policy: downed/revive or death/respawn, respawn location, action restrictions, inventory cost and behavior if the host dies/loads. A single-player checkpoint load must not silently leave the peer in an old world epoch.

**Gate:** at least 20 controlled kills initiated from each role yield one accepted damage/death sequence and one reward per target, including duplicate/lost/reordered requests, simultaneous lethal hits and reconnect. Both peers agree on health and living/dead state. Player death/respawn and load paths clear old actors, requests and markers without replaying rewards. No general combat claim until the published supported weapon/action matrix passes.

## M7 — streaming, late join and recovery

Separate a replicated object's logical lifetime from whether its local engine entity is streamed in. Snapshot only the supported interest set, apply it at a defined revision, then apply later deltas. Retain tombstones or equivalent generation protection long enough to reject delayed resurrection. Late appearance/pose callbacks must validate the current generation.

Define the first separation rule: bounded radius or same streaming region, with visible unsupported-state handling. Expand after testing interiors, elevators, fast travel and widely separated players. Do not make invisible actors permanent simply to avoid a streaming bug. Set measurable caps on active/pending entities, memory and bandwidth before testing.

**Gate:** new-process join/rejoin, intentional disconnect, save load, map pause, fast travel and world reset converge on a fresh snapshot; no stale seats/NPCs/damage/markers remain. Include leave during pending spawn/mount/delete and long network blackout. Log sequence/epoch transitions and actual completion times. Prior quickload success is useful evidence, not a substitute for all of these cases.

## M8 — world objects, inventory and rewards

Start with an explicit list of supported doors, containers and switches. Use stable world identity plus revision and an idempotent interaction transaction. Decide which machine owns open/locked/consumed state and how late join restores it. Do not broadcast arbitrary game object handles or arbitrary method names.

Define inventory semantics before implementation: personal equipment versus shared container contents; ownership of loot; host-authoritative grants; currency, ammo and quest-item policy. Treat removal and grant as one recoverable transaction so disconnect/retry cannot duplicate or lose an item. Engine `GiveItem`/`RemoveItem` APIs are only local operations, not distributed transactions.

**Gate:** simultaneous looting, duplicate requests, full inventory, missing mod item, disconnect between debit/credit and snapshot recovery preserve the declared item/currency totals. Each supported door/container reaches the same revision. Existing single-player saves are backed up and session-owned changes are distinguishable from permanent campaign progress.

## M9 — quests, dialogue, scenes and persistence

Inventory/quest APIs expose local facts and item operations, but their availability does not prove that copying them synchronizes scene systems, quest entities or irreversible side effects. Start with one allowlisted quest adapter on disposable saves. Specify leader choice, dialogue selection, scene participation, time dilation, cutscene cameras, scripted teleports, fast travel and join restrictions. Observe the host's accepted progress, rather than executing quest side effects independently on both machines.

Maintain a compatibility matrix by quest/stage, supported game/DLC version and save preconditions. Track checkpoints and transaction revisions sufficient to distinguish retry from a new reward or scene. Initially reject joining during unsupported scenes and never claim generic quest sync based only on a fact hash.

**Gate:** the same supported quest progresses from a declared starting checkpoint to completion from each role, with agreed decisions/rewards and valid subsequent reload; interruptions at each irreversible step do not duplicate rewards or softlock either save. A clean save audit and rollback are part of the result. General campaign support remains incomplete while untested quest classes lack adapters.

## M10 — security, real links and release evidence

Before public exposure, test admission credentials, endpoint/session binding, replay, spoofed ownership, malformed lengths/types/numbers, resource floods and per-domain abuse at the real socket boundary. Keep secrets out of logs and packages. Transport encryption can protect packets but cannot make a modified host honest; document the host-trust model. Evaluate an established maintained transport only for a demonstrated gap, without discarding the current tested CB77 integration opportunistically.

Run distinct test classes and label them accurately:

1. Deterministic unit/property tests for policy and lifecycle, plus cross-language wire fixtures.
2. Real-socket headless clients through the actual relay, including loss, jitter, duplication, reorder and reconnect.
3. Two local game instances with identical manifests and recorded actual entity telemetry.
4. Two real PCs through an identified reachable relay, each role assignment, actual clock-offset uncertainty and observed NAT/link behavior.
5. Uninterrupted qualification runs: at least 30 minutes per declared simulated profile, then a one-hour two-PC session covering the supported sandbox features. Interactive fault scenarios are separate runs, not counted as a clean soak.

The existing 115 ms +/-20 ms / 1% and 160 ms +/-30 ms / 2% test profiles are configured forwarding-delay/loss conditions; report the measured RTT and visual delay rather than calling a local profile a real transatlantic connection. Record frame-time percentiles, bandwidth, queue/memory caps, reliable backlog, corrections, actual-state error, crashes and cleanup. Capture gaps, restarts and unobserved intervals invalidate duration claims.

**Release gate:** every advertised domain passes its own matrix, two-PC qualification is recorded, mismatched builds fail visibly, logs and hashes identify the exact package, installation/rollback works, and remaining restrictions are user-visible. Passing transport, CI or a single NPC path does not satisfy this release gate.

## User-video follow-up — v0.0.37 (2026-10-04)

The [timestamped fix and research backlog](PLAYTEST_V37_BACKLOG.md) records Kyle's 105.67-second recording and new requirements. All entries remain open: vehicle physics/camera disruption (V37-01), exact car model/appearance (02), confirmed shared driver/passenger seats (03), movement correction/visual-delay measurement (04), remote ADS animation and aim transitions (05), teammate icon with a facing arrow instead of a waypoint (06), pause/focus reconciliation (07), and scoped ambient-world consistency (08). ADS and directional-marker details extend M2; car instability/identity/seats extend M3/M4 and M6; interruption recovery applies throughout M7. Prioritize reproducing the final vehicle instability before claiming a shared ride. Video samples are qualitative evidence, not latency measurements or proof of common engine identities. No implementation or new runtime release accompanies this update.

## Immediate work queue

1. Continue from verified source 856721b and its consolidated build/runtime/authority tests. Prepare the isolated game-side authority dispatcher and seat observer; preserve v37 as the installed baseline until a separately verified deployment is made.
2. Use the seat observer to resolve actual mount-completion semantics for one known car; compile/API checks precede any live run. Independently run a matched player locomotion experiment against the existing A10 limitation.
3. Connect proven policy to a single stationary owned car, including stable parked identity and measured cleanup. Add moving passengers only after stationary confirmation.
4. Expand the controlled NPC into a bounded encounter and one authoritative damage/death path. Carry restart/streaming/idempotency checks into every increment.
5. Qualify the sandbox on two PCs before broad traffic, arbitrary vehicles or campaign expansion. Record unsupported areas as open work rather than quietly widening the completion claim.

Source basis and engine uncertainties are in [RESEARCH_NOTES.md](RESEARCH_NOTES.md). Integration/provenance and contributor workflow are maintained by the repository's root documentation.
