# Cyberpunk multiplayer landscape and development shortcuts

Expanded follow-up: [Multiplayer research atlas](research/README.md) organizes reported fixes, unresolved issues and proposed experiments by symptom. Six chapters and a source ledger add deeper bug evidence, NightCityMP, REPLAY/BlackICE and Archipelago.

Research date: **2026-10-04 (PDT)**. Scope: research, documentation and plans only, before Claude Code takes over. No games/launcher executed, dependencies installed or competitor implementation imported. Our pre-review source checkpoint is `d22a9ee`; gameplay remains **v0.0.37 / CP2077CoopNet 0.2.0-alpha.5**.

## Conclusions

OPEN//77 and CyberMP expose useful platform interfaces and examples, but their inspected GitHub organizations do not expose a complete native multiplayer implementation. Documentation establishes intended contracts, not independently measured gameplay quality. Cyberverse offers public native/game code, but its README warns that it is incomplete and developer-oriented.

The fastest useful route is independent implementation supported by precise API contracts, failed-experiment records and small engine probes. Our immediate bottlenecks are actual vehicle/seat/animation behavior, movement quality and lifecycle recovery. A transport rewrite, server browser or roleplay economy would not resolve these by itself.

Keep source links for research verification; they are not contributor credits or branding. No competitor was added to the README credits. If implementation is ever reused, retain its required notices and provenance rather than disguising copied code. This review adopts ideas only.

## Current landscape

| Project | Public evidence checked | Relevance and limits |
|---|---|---|
| [OPEN//77](https://open2077.net/) / [Open2077](https://github.com/Open2077) | Alpha platform; five public org repositories. Oct 4 download page lists launcher 2.31.21+op77.126 and separate server .125. Requires game 2.31, Phantom Liberty and Alpha access to play. | Extensive Lua/platform documentation. Public tools/examples are not the core game-engine implementation; no gameplay independently tested here. |
| [CyberMP](https://cyber.mp/) / [Cyber-MP](https://github.com/Cyber-MP) | Website says semi-open beta; download requires tester access. Public Types, RPC, Freeroam and profile repos. | Useful TypeScript contracts/tooling; no exposed core replication implementation in the inspected org. Distinct from Tilted Phoques' similarly named project. |
| [Cyberverse](https://github.com/TDUniverse/Cyberverse) | MIT, HEAD 645d1780: June 30 commit, Aug 12 repository push activity. README targets game 2.1 and warns of missing pieces. | Public C++/RED4ext/redscript and managed-server layers. Prior research corroborated AI teleport; no fresh 2.31 compatibility claim. |
| [Choomlink](https://github.com/FOX-cOSMIC/choomlink) | README says pre-development, eight-player target; choomlink-core is a Cyberverse fork, with Aug 12 push metadata. | Planning effort, not proven eight-player gameplay. |
| [Tilted Phoques CyberpunkMP](https://github.com/tiltedphoques/CyberpunkMP) | Not archived; last push 2024-12-21; custom license. | Prior research recorded restrictive competitor-analysis terms. Implementation remains excluded; only public status metadata revisited here. |
| [Bukczyk CP2077-Coop](https://github.com/Bukczyk/CP2077-Coop) | Previously inspected checkpoint 20eb125; legacy prototype and unfinished typed-session migration. | See [checkpoint comparison](BUKCZYK_CHECKPOINT_COMPARISON.md); no newer integrated runtime established. |

This is a bounded survey, not an exhaustive inventory of private or abandoned projects. Activity, advertised features, available source and measured results are different evidence levels.

## OPEN//77: actionable public design evidence

**Authority boundary.** Its [platform guide](https://open2077.net/docs/platform) describes a dedicated server holding accepted world state, clients rendering it, and separately packaged resources. That differs from our host-simulates-world/relay-routes design. Server authority does not imply a headless REDengine physics simulation. Retain our current authority boundary.

**Vehicle ownership.** The [vehicle guide](https://open2077.net/docs/vehicles) describes temporary physics ownership, epochs, sequenced motion, reliable lifecycle and separate observer presentation. It reports that kinematic replicas failed to recover drivable physics and direct mesh-body manipulation crashed a second client; it describes whole-vehicle movement instead. These are vendor-reported observations, not our validated fixes. Investigate ownership/physics conflict in our failure video without importing private addresses or applying global physics flags.

**Seat transitions.** [Changing seats](https://open2077.net/docs/vehicle-seat-switching) retains the source occupant while reserving the destination, correlates transitions and confirms after completion. Cancellation may require visual return. Its numeric aliases differ from CyberMP's Types: our bridge needs a validated per-model slot mapping, never blind enum copying. Boarding, seat switching and forced placement require separate tests.

**Actions and ADS.** The [API index](https://open2077.net/docs/api) exposes graph-derived aiming, including toggle aim. Its [animation guide](https://open2077.net/docs/rp-animations) and [held-action reference](https://github.com/Open2077/open77-rp-examples/blob/5dcdad29f5b632ec93fe2343f29facb0a715a034/docs/held-actions.md) separate action identity, presentation and inventory effects, with interruption cleanup. Probe actual aim state and action generations in our runtime. These references do not expose an ADS adapter for our puppet or prove its visual quality.

**Markers.** [Custom blips](https://open2077.net/docs/custom-blip-icons) require a native SVG adapter and fallback; a Lua update alone cannot supply it. Research our actual mappin/widget path. A custom icon does not prove a correctly rotating player arrow: body heading, map rotation and staleness remain separate checks.

**Readiness.** The [join gate](https://open2077.net/docs/readiness-gate) aggregates resource readiness with session generations and timeouts. For us, distinguish engine-loaded, admitted, baseline-applied and gameplay-ready. Decide explicitly which expired holds abort joining; do not blindly inherit another platform's timeout behavior.

**Content and session rules.** The [September 4 post](https://open2077.net/devblog/2026-09-04-faster-ui-smoother-joining) describes relaunch for boot-time assets and preserving the core stack when applying server content. The [September 10 post](https://open2077.net/devblog/2026-09-10-smarter-launcher-sturdier-servers) reports blocking solo save/load/time-skip during multiplayer. Our follow-up is a compatibility/readiness specification and reversible session policy, not installing content or changing user controls now.

## CyberMP: accessible contracts, hidden core

The [download page](https://cyber.mp/download) requires tester access. `docs.cyber.mp` and `/rpc` were inaccessible through the browser tool; GitHub supplied the following evidence. None establishes their engine hooks, interpolation algorithm, tick rate or core wire security.

- [Vehicle types](https://github.com/Cyber-MP/CyberMP-Types/blob/e8625b0ea66d55fcb702e55402714d2693c9bbd5/packages/server/src/vehicles.ts) distinguish model/appearance, linear/angular velocity, named seats and previous occupants. These inform our matching/observer requirements; getters do not prove mounting completion.
- [Player types](https://github.com/Cyber-MP/CyberMP-Types/blob/e8625b0ea66d55fcb702e55402714d2693c9bbd5/packages/server/src/players.ts) associate players with vehicles and collections. Use our own identities and per-peer lifecycle.
- [RPC](https://github.com/Cyber-MP/CyberMP-RPC/tree/3ed25183a0ed72fdbdd1fc116192bfc35b69e94b) separates browser/client/server packages. Selected contracts define request IDs and failures for timeout, authorization, invalid input and too many pending requests. Apply bounded control-operation contracts; do not put high-frequency pose delivery through request-response RPC.
- [Freeroam](https://github.com/Cyber-MP/CyberMP-Freeroam/tree/42e5fa0441c3ed3962e9eba3b70eab4e97255298) describes shared types, hot reload and client/server/UI separation. Adopt the iteration discipline, not a TypeScript/React rewrite before shared rides work.

## Highest-value process improvement: a local API knowledge pack

OPEN//77's MIT [Devkit](https://github.com/Open2077/open77-devkit/tree/1e2b1b6b76f2f3b3039a3ec2dad01490cc178b1d) documents build-specific lookup, availability checks and client/server validation. Its [agent guide](https://open2077.net/docs/agents) reinforces version-aware answers. Independently apply that approach to our project using Markdown/JSON first, without installing an external MCP or new service.

Proposed cards for **our** bridge: exact signature, runtime/thread, game/dependency version, units, ownership, return semantics, asynchronous completion query, cleanup, source revision and evidence level (declared/compiled/live-tested). Start with mount/unmount, vehicle transform/velocity, avatar commands, aim state and mappins. Explicitly label unavailable APIs.

Maintain a failed-experiment index: origin spawning, tag removal before entity disappearance, A10 sprint lag, unmatched steering comparisons and the final vehicle/camera failure. Every implementation task should cite its API evidence and one measurable gate. This is planned work, not a completed tool.

## Next experiments, after implementation is requested

| Order | Deliverable | Gate |
|---|---|---|
| 1 | API cards and vehicle telemetry specification | Verified signatures; record identity/record/appearance, simulator/epoch, car-origin pose/velocities, mount parent/slot, all correction requests and pause state. No mutation yet. |
| 2 | Isolate V37-01 failure with one parked owned car | Reproduce, or explicitly fail to reproduce, the 01:33-01:45 video behavior. Vary one condition at a time; diagnose before changing physics flags. |
| 3 | Matching car and stationary driver/passenger | Same identity/model/appearance, observed exact seats, no duplicate standing puppet; safe cancel/timeout/exit and parked lifetime. |
| 4 | Moving shared ride, initially host-driven | Straight/turn/stop/reverse/slope; existing pose/orientation targets, attached passenger, no unexplained launch/camera trapping, departure cleanup. |
| 5 | Separate ADS and directional-marker probes | Both-role aim entry/release/interruption; arrow handles stationary turns, map rotation and stale state. No damage-sync claim. |
| 6 | Readiness and pause/load recovery | Old callbacks cannot act on a new generation; unsupported states are visible and recoverable. |
| 7 | Measured locomotion, then snapshot-rate comparison | Same build/route, actual pose and 12-second sprint; report present-time lag, delayed-timeline error, corrections and frame cost. More packets alone is not success. |
| 8 | Two-PC qualification before scale/traffic/quests | Pinned topology/builds, sustained duration, measured errors and clean recovery. |

Keep CB77 and one native poll owner. Broad population suppression and retained steering stay off. These refine [the existing plan](MULTIPLAYER_PLAN.md) and [video backlog](PLAYTEST_V37_BACKLOG.md), not a platform replacement or delivery promise.

## Provenance and reuse boundaries

No implementation was copied into our source. Inspect exact licenses and dependencies before any later reuse; this is a provenance record, not a legal-clearance claim.

| Repository | Pinned HEAD | Observed license / handling |
|---|---|---|
| Open2077/open77-devkit | 1e2b1b6b76f2f3b3039a3ec2dad01490cc178b1d | MIT; requires notices for copied substantial portions. Design reference only. |
| Open2077/open77-rp-examples | 5dcdad29f5b632ec93fe2343f29facb0a715a034 | MIT; foreign runtime APIs, not drop-in code. |
| Open2077/freeroam | d3f2a7f96ea3b25a614c0f6892798429d8406b69 | PolyForm Noncommercial 1.0.0, not MIT; license inspected, implementation adoption excluded. |
| Open2077/open77-app | 622c8c09fbd9d73db78eadd590592f49f46c8c5c | No license identified in inspected metadata/root files; no implementation reuse. |
| Open2077/polyzone | Not source-reviewed | No license identified in org metadata; defer reuse. |
| Cyber-MP/CyberMP-Types | e8625b0ea66d55fcb702e55402714d2693c9bbd5 | MIT; selected declarations inspected. |
| Cyber-MP/CyberMP-RPC | 3ed25183a0ed72fdbdd1fc116192bfc35b69e94b | MIT; selected contracts inspected, no package installed. |
| Cyber-MP/CyberMP-Freeroam | 42e5fa0441c3ed3962e9eba3b70eab4e97255298 | MIT repo license; do not assume blanket clearance for every included third-party asset/binary. README reviewed. |
| TDUniverse/Cyberverse | 645d17809f0262586ff8687f1896984e821b5e6f | MIT; prior mechanism review, no fresh compatibility run. |
| FOX-cOSMIC/choomlink | 7f5d38151e0336fe69b2d8b9e6a9b4ce23ca7c41 | Planning repo without identified license; core fork separately reports MIT. No reuse. |

## Supplied launcher: identity check only

`D:\Downloads\Open77Launcher.exe`: 60,466,237 bytes; file version `2.31.21.0`; Authenticode **NotSigned**. SHA256:

`8884b8b8d7e3171d4d8accca8085ac6e8fc249ad2892fad43691733ef3012aea`

Matches the [official download page](https://open2077.net/download) at review time (launcher 2.31.21+op77.126). This establishes file correspondence, not safety or gameplay quality. The executable was not launched, decompiled, installed, uploaded or added to our package.

## Evidence and continuation

Local evidence: `D:\Downloads\syncfix\bench-artifacts\20261004-multiplayer-landscape`: pinned metadata/trees, selected source references, license files, public guide snapshots and review manifest. No new build, benchmark or live gameplay test occurred. Recheck changing web claims before implementation.

Read [Claude handoff](CLAUDE_HANDOFF.md) and `G:\CyberpunkMP\handoff.md`. Latest user scope remains **documentation only**; the plans above do not authorize starting game tests or implementation now.
