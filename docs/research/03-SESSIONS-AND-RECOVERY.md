# 3. Sessions, streaming, focus and recovery

[Atlas](README.md) · V37-07 and future create/join flow. Proposed checks are not implementation authorization.

## SES-01: request fails before the player body exists

**Their result — source-confirmed change.** In [CyberMP RPC commit 96db612](https://github.com/Cyber-MP/CyberMP-RPC/commit/96db6123bf177aae3a906ad853afc14c92805b61), the client databus replaces entity-dependent player-ID lookup with a local network-player-ID query. The commit also raises the client-types dependency. [Commit 08cde4d](https://github.com/Cyber-MP/CyberMP-RPC/commit/08cde4dbdb63d9432b282d85c44a596aa5794ded) rejects invalid -1 targets in server client-call paths. Both public diffs were inspected and saved locally; neither was executed.

**Our proposal.** Separate authenticated membership from body incarnation. Session-level messages may need membership before a puppet exists; teleport, mounting and animation require an actual ready body. Validate the relevant readiness level per operation rather than blocking everything behind one boolean. Bind delayed replies to membership and body generations.

**Gate.** Test admission before body creation, body replacement without changing membership, and disconnect/rejoin while requests are pending. Invalid targets fail explicitly; old callbacks cannot affect a new player.

## SES-02: teleport accepted but player falls or returns to the save spawn

**Their result — documented failure and completion contract.** OPEN//77's [travel guide](https://open2077.net/docs/travel) explains that a raw teleport executes later and can land over unstreamed ground, triggering the game's under-world fallback. Its settled operation observes position, grounded state and absence of falling over consecutive frames; a weaker near result is distinct from fully settled. Superseded and cancelled operations have explicit outcomes.

**Our proposal.** A successful API call or same-frame position read is not an arrival check. Track request generation, actual destination occupancy, floor/ground readiness, timeout and cancellation. Do not repeatedly force transforms onto unloaded geometry.

**Gate.** After separately authorized tests, compare nearby and distant travel, a deliberately unavailable destination, supersession and timeout. No downstream vehicle spawn/mount begins from a merely accepted teleport. Preserve the original user save and report failed settling honestly.

## SES-03: keyboard focus or camera remains captured after an event

**Their result — open issues.** CyberMP Freeroam [#100](https://github.com/Cyber-MP/CyberMP-Freeroam/issues/100) tracks browser focus after spawn/events; [#98](https://github.com/Cyber-MP/CyberMP-Freeroam/issues/98) tracks intermittent spectator problems. [#73](https://github.com/Cyber-MP/CyberMP-Freeroam/issues/73) lists chat/menu conflicts, race camera/spawn problems and other test failures. All were open at review. There is no verified reusable fix in these issue bodies.

**Our proposal.** Log foreground window, modal UI, input owner, camera mode, pause state and entity readiness alongside packet activity. Network traffic can continue while the client is unable to act. Test Alt-Tab, pause, overlay open/close and disconnect during each modal state. Gate: no stuck input/camera and no duplicate recovery action; explicitly distinguish host pausing simulation from joiner losing focus.

## SES-04: time dilation causes peers to disagree

**Their result — documented limitation.** The [time-scale guide](https://open2077.net/docs/world-time) separates simulation speed from day-clock speed. Its slowdown is cinematic and affects the local body, not an individual Sandevistan advantage. It records owner-scoped claims and cleanup on expiry/teardown; writing dilation before a body exists is refused after a reported crash. Effective clock rates must be measured rather than trusting a requested scalar.

**Our proposal.** First specify supported multiplayer pause/time behavior without implementing blanket suppression. Observe game-clock change against a monotonic clock and tag samples from paused/dilated intervals. Gameplay timers and transport timeouts need explicit clock domains. Gate: session exit restores prior behavior and no stale time-control claim remains. Individual slow-motion cyberware remains an unsolved gameplay design item.

## Recovery state model to document in our bridge

Membership admitted -> content compatible -> live body -> destination settled -> world baseline applied -> gameplay ready. This is our proposed decomposition, not a replacement wire protocol. Give each stage an observable condition, timeout, cleanup path and generation. A timeout should not silently promote readiness. Consult [the earlier readiness research](../MULTIPLAYER_LANDSCAPE_2026-10-04.md) before choosing policies.
