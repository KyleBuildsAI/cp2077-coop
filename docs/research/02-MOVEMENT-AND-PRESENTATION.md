# 2. Movement, ADS and teammate markers

[Atlas](README.md) · [Our video backlog](../PLAYTEST_V37_BACKLOG.md): V37-04/05/06. No gameplay fix implemented by this research.

## MOT-01: sprint lag, visible corrections and unstable look direction

**Their result — reported improvement, algorithm unknown.** OPEN//77's [September 23 update](https://open2077.net/devblog/2026-09-23-netcode-repair-status-upgrade) reports timed replication, high-speed motion and hit-validation improvements. Its [October 4 update](https://open2077.net/devblog/2026-10-04-smoother-sync-and-server-status) reports vehicle steering presentation and remote look stabilization. Neither publishes interpolation windows, clock correction, error distributions or a reproducible before/after benchmark.

**Our proposal.** Separate packet age, delayed playback error, present-time lag, body heading, camera aim and animation phase. Record sender sample/sequence, receiver arrival, playback target and rendered/native pose on the same timeline. A low RTT does not eliminate buffered playback or slow engine actuation.

**Gate.** Repeat the existing 12-second sprint and turns on the same build/route; report correction count, max/P95 error, packet loss/jitter and frame cost. Compare 20/30/60 Hz only after that baseline. The existing A10 waiver remains a failure. Keep retained steering off until a matched experiment establishes improvement without instability.

## ANI-01: other player aims but the puppet does not

**Their result — documented state contract.** [Player utilities](https://open2077.net/docs/player-utilities) reads graph-produced aim, including toggle aim. It distinguishes shooting state from confirmed damage, and unavailable remote state from false. Remote swimming/diving is explicitly not replicated in that guide. These are their APIs, not functions we can call in CP2077CoopNet.

**Unresolved corroborating issue.** [Cyberverse #6](https://github.com/TDUniverse/Cyberverse/issues/6) remains open and notes that a jump keypress is not actual jumping; locomotion can sometimes be inferred, but special states need explicit handling. [Cyberverse #4](https://github.com/TDUniverse/Cyberverse/issues/4) separately tracks weapon identity/equipping and explicitly separates damage scope.

**Our proposal.** Build capability cards for reading actual local aim/weapon state and driving the remote puppet's supported animation. Include aim entry/release, weapon type, graph availability, epoch and interruption. Do not use right-mouse-down alone. Test hold aim, toggle aim, reload, weapon swap, crouch, sprint interruption, death and disconnect in both roles. Later add vehicle modes separately.

**Gate.** Both screens show correct sustained aim and release without sticky poses; unavailable state expires safely and cannot authorize damage. Record visual transition delay independently of networking RTT.

## MAP-01: teammate displayed as a waypoint rather than a player

**Their result — documented contract, arrow still unknown.** [Blips documentation](https://open2077.net/docs/blips) supports attached entity markers and a remote-player alias, while routable positional waypoints have a fixed custom-waypoint variant and cannot follow an entity. Marker IDs are opaque 64-bit strings; the guide warns against numeric conversion. Native Ink/SVG icons require its adapter. The inspected API does not establish a heading-rotation control.

**Our proposal.** Split the feature into player identity/icon, continuously updated position, facing arrow and optional navigation destination. Verify an appropriate native variant in our installed build; do not copy their enum number. Keep body facing distinct from velocity and camera aim. A stationary player must still turn their arrow.

**Gate.** Test all compass directions, rotating minimap, fullscreen map, entering a car, leaving streaming range, stale packets and reconnect. Exactly one owned marker survives each valid player generation; remove it on departure. Ping text must identify RTT and never masquerade as measured animation latency. A navigation destination, if later offered, should be an explicit separate action.

## ANI-02: upper-body action breaks walking or first-person camera

**Their result — reported fixes.** The [September 19 update](https://open2077.net/devblog/2026-09-19-walkable-emotes-and-stability) describes moving upper-body actions, corrected first-person carry offsets and camera behavior. It does not expose an ADS blending implementation.

**Our proposal.** Qualify local first-person, local third-person and remote puppet presentation separately. An animation that looks correct on the remote skeleton may use incompatible offsets on the local arms. Start with the existing stand-in rather than replacing avatar identity and animation simultaneously. Gate: walking and aim can coexist where intended, and ending an action restores camera/locomotion ownership.
