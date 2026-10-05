# v0.0.37 user video: fix and research backlog

Recorded test supplied by Kyle; reviewed 2026-10-04. **All items below are OPEN. This update changes documentation only.** It does not implement fixes, create a new build, or establish full multiplayer readiness. Dependencies and overall release gates remain in [MULTIPLAYER_PLAN.md](MULTIPLAYER_PLAN.md).

## Evidence and limits

Source: `D:\Videos\CP-COOP v0.0.37.mp4`, 105.67 seconds, 30 fps, 2730x1134, 170,588,786 bytes. Review used timestamped decoded frames across the entire recording, with denser sampling of movement, weapon presentation, driving and the final disruption. It is a visual frame review, not an audio review or a frame-accurate latency measurement. Local contact sheets and source hash are preserved under `D:\Downloads\syncfix\bench-artifacts\v37-user-video-review`.

Use **left/right view** for video evidence: prior setup assigned host left and joiner right, but the recording has no persistent role labels. Desktop overlays, window switching and in-game pause menus interrupt the test. Do not count this as an uninterrupted soak, assume both simulations are continuously advancing, or infer engine entity identity from screen position alone.

| Video time | Observed evidence | Interpretation / follow-up |
|---|---|---|
| 00:00-00:18, map visible at 00:12 | Remote character has an in-world location indicator; full map is opened. | User requests a teammate icon plus facing arrow. Existing marker documentation identifies the current implementation as CustomPosition/Pinned Location; this review does not establish heading support. |
| 00:20-00:30 | Right camera changes position while the left remote character follows; marker and rendered character are visibly separated at some samples. | Supports inspecting movement/marker timing together. Kyle reports jumps and latency; exact delay, correction frequency and root cause remain unmeasured. |
| 00:30-00:44 | Weapon presentation and remote standing/moving poses are visible. | ADS replication is an explicit new user requirement. Sampled frames do not isolate a held ADS transition reliably enough to declare a particular ADS failure proven. |
| 00:54-01:20 | Right view drives a blue sports car; left view shows a visibly different dark car moving during that sequence. Nearby vehicles also differ. | Strong visual reproduction candidate for model/appearance matching and vehicle pose work. Confirm net IDs, local entity IDs and records before attributing every visible car to a proxy or ambient traffic. |
| 00:48-00:54 and 01:22-01:31 | One view is paused while the other is active. A remote on-foot character is visible near the left camera at 01:22-01:24 while the right paused view still shows the car. | Investigate pause/unfocus effects on vehicle state and avatar visibility. A single sampled pose does not prove duplicate occupancy or failed dismount. |
| 01:33-01:45 | Vehicles become airborne/rotate, the cameras clip through or are obscured by geometry, and the right view shows explosion/debris around 01:39. | Highest-priority vehicle reproduction case. Possible competing physics, proxy overlap, mount/transform feedback or other forces are hypotheses, not diagnosed causes. A successful shared passenger ride is not established. |

## Requested fixes and research tickets

### V37-01 — Vehicle physics instability and collision feedback (high; M3/M4/M6)

- [ ] Reproduce the final 01:33-01:45 sequence on backed-up test saves; start with one parked owned car in a clear area and add interactions incrementally.
- [ ] Log network identity/incarnation, local entity ID, authoritative simulator, full car-origin transform, velocity, every correction/teleport request, mount parent/seat, collision/damage and pause/focus changes.
- [ ] Research whether local physics fights received placement, whether overlapping replicas collide, and whether a player's mounted pose is fed back into car placement. Confirm each hypothesis separately; do not globally disable ambient vehicle physics.
- [ ] Gate: repeated entry/exit, stopping, turns and contact with a supported obstacle produce no unexplained launch, runaway corrections or camera trapping; both peers agree on supported damage/destruction outcomes. General collision fidelity remains a separate M6 gate.

### V37-02 — Match the actual car on both peers (high; M3)

- [ ] Give the shared car stable host-owned identity; transmit validated record/model and appearance/paint/variant rather than selecting a visually unrelated fallback by index.
- [ ] Distinguish the owned car from independently spawned ambient cars; record identity mapping before declaring two cars equivalent.
- [ ] Research supported record/appearance serialization and missing-asset behavior. Choose an explicit visible fallback/rejection for unavailable modded vehicles.
- [ ] Gate: same supported model and appearance on both peers before entry, while driving, after parking/re-entry and reconnect, with one live replica per network identity.

### V37-03 — Two players in one car (high; M3/M4)

- [ ] Implement and observe driver/passenger seat arbitration, actual mount completion and correct local parent/slot. A seat grant alone is not completion.
- [ ] Synchronize enter, seated, exit and interrupted transitions; suppress the free-standing remote avatar only when actual mounted state warrants it, and restore it after exit.
- [ ] Research passenger attachment to the moving replica, camera/collision interactions and a single simulation owner. Start host driving; driver transfer needs explicit revocation and a new authority generation.
- [ ] Gate: one driver plus one passenger remain in the same agreed car through start/stop/turn/reverse/slope, stopped exit/re-entry, pause/resume and disconnect. Test both role assignments when supported. Simultaneous driver requests cannot create two drivers; a disconnect cannot trap the occupant.

### V37-04 — Smooth movement and measurable visual delay (high; M2)

- [ ] Retain the existing A10 sprint/correction issue; add this clip as user evidence rather than filing an unrelated duplicate.
- [ ] Record source pose, accepted snapshot, chosen delayed render sample and actual remote avatar pose on a common timeline. Record frame times, correction requests, network RTT, foreground/background state and pause state separately.
- [ ] Research interpolation age, puppet steering speed, stop/turn transitions and late teleport application using same-build/same-route comparisons. Do not call RTT the animation or visual delay.
- [ ] Gate: meet existing walk/run p95 <=0.5 m and sprint p95 <=1.5 m targets against the declared render timeline, report present-time lag and corrections, and repeat 12-second sprint, stop, turn, jump and crouch sequences in both directions. No new latency number is inferred from this video.

### V37-05 — Visible aim-down-sights animation (requested; M2, combat outcomes in M6)

- [ ] When either player aims down sights, the other sees the equipped weapon raised into a matching aim pose; leaving ADS lowers it correctly.
- [ ] Research reliable local ADS entry/exit observation, weapon identity, remote animation/stance support, aim yaw/pitch versus body heading, and upper-body blending while walking/crouching. Distinguish hip fire, ADS, reload and weapon switching.
- [ ] Reset stale aim on unequip, death, vehicle entry, reload/reconnect and session change. A missed release packet must not leave permanent aiming.
- [ ] Gate: record idle -> ADS -> release from both roles with supported pistol and long gun, standing and moving/crouched, plus reload/switch/interruption. Remote weapon, direction and transitions visibly agree; visual animation does not count as authoritative hit/damage synchronization.

### V37-06 — Player icon with facing arrow (requested; M2)

- [ ] Replace the ordinary waypoint/Pinned Location presentation with a recognizable teammate/player symbol and a heading arrow on minimap/full map; retain useful in-world identification without confusing it with a personal navigation waypoint.
- [ ] Research an actual supported player-mappin/controller or a scoped custom widget. The earlier generic CPO variant did not render correctly; do not repeat that approach without resolving its required class/controller.
- [ ] Define arrow orientation as remote body facing, not travel direction; account for minimap rotation and heading wraparound. Keep weapon aim direction separate if the body and aim can differ. Specify seated orientation and label stale heading.
- [ ] Keep timestamped marker position and heading coherent, document latest-snapshot versus delayed-avatar placement, and prevent surprising marker/avatar separation. Keep RTT accurately labeled in the panel; never present it as visual delay.
- [ ] Gate: stationary turning, strafing, walking backward, 359-to-0-degree rotation, map rotation/zoom, vehicle use, streaming, reload/reconnect and departure work on both peers; exactly one teammate marker, no stale arrow or waypoint routing side effects.

### V37-07 — Pause, focus and avatar/vehicle state transitions (high; M2/M4/M7)

- [ ] Reproduce one player opening the map/menu or switching windows while walking, aiming, entering, seated or exiting a car.
- [ ] Research whether simulation or sending pauses, how stale state is presented, and whether resume applies old transforms/mount commands. Define a supported session pause policy before treating these as ordinary packet loss.
- [ ] Gate: no stale seat, orphan on-foot proxy, accumulated teleport burst or launched car on resume; logs distinguish inactive/paused from disconnected and recovery reconciles current state.

### V37-08 — Shared traffic/world consistency (existing expansion; M5/M6/M7)

- [ ] Preserve ambient traffic/NPC authority as separate unfinished work: different nearby cars/populations can create different collision contexts even when the owned car is matched.
- [ ] Research stable identity and scoped adoption/replication for supported nearby entities. Do not enable blanket joiner population suppression.
- [ ] Gate: a bounded supported encounter agrees on identities and collision/damage results before broad traffic claims. Independent ambient cars in this clip do not establish a particular identity-mapping bug.

## Next research order and rollout boundaries

First isolate vehicle instability and state transitions, then prove matching identity plus stationary seats, then moving passengers. Continue the existing measured movement work alongside ADS and directional-marker investigations. Keep full appearance/loadout, authoritative combat/death, bounded NPC AI, world/inventory, quests/persistence and two-PC qualification on the main roadmap; none is closed by this review.

Each future ticket must record source/build, reproducible steps, hypothesis, smallest experiment, both-role evidence and a pass/fail result. This request only adds fix/research work: no new game launch, settings change, implementation, internet research claim or runtime release is part of this documentation update.
