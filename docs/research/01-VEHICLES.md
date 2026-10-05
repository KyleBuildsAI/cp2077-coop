# 1. Vehicles, seats and camera failures

[Atlas](README.md) · Our symptoms: [V37-01/02/03](../PLAYTEST_V37_BACKLOG.md). All experiments below are planned, not performed.

## VEH-01: explosion after streaming back in

**Their result — reported fix.** OPEN//77's [August 25 update](https://open2077.net/devblog/2026-08-25-server-banners-and-world-sync) identifies uninitialized health on vehicle restream as the cause of exploding fleets; it reports initializing health before returning cars to view.

**Our status.** The video shows airborne vehicles and an explosion late in the test. We have not established restreaming, an invalid health read or the same root cause. Do not label this our diagnosis.

**Proposed check.** Record health validity/value, native entity generation, spawn/stream state, damage events and first applied baseline before and after leaving/re-entering range. Compare a continuously visible parked car with the same car after restream. Do not overwrite health every frame to hide the failure. Gate: health never comes from uninitialized data and a passive restream does not create damage or an explosion.

## VEH-02: two simulations fight over one car

**Their result — documented contract / prior negative evidence.** The [vehicle guide](https://open2077.net/docs/vehicles) describes leased simulation ownership and observer presentation. It reports failed drivable recovery with kinematic replicas and crashes from direct mesh-body manipulation. Whole-vehicle movement is their documented alternative; the native implementation is not exposed here.

**Our proposal.** Log every writer of the car transform and every ownership/physics-mode change. Distinguish local physics, replica correction, seat attachment and collision impulses. Test one observer with corrections disabled only in an isolated experiment, then restore the prior configuration. A disappearing explosion alone does not establish acceptable driving.

**Gate.** Exactly one authorized simulator per generation; observer corrections cannot feed back into accepted physics or damage. Reacquiring control must restore drivable behavior. Keep any future driver simulation lease subordinate to our host's accepted world identity and outcomes.

## VEH-03: handover snaps backward, freezes or disconnects

**Their result — reported fixes.** The [September 28 update](https://open2077.net/devblog/2026-09-28-ai-traffic-stability-pass) describes retaining better placement information at handoff and recovering failed placements. The [September 30 update](https://open2077.net/devblog/2026-09-30-combat-npcs-prediction-pass) reports occupied-car handover, driver reclaim and avoiding unhealthy simulators. Neither gives the full election algorithm. The [September 25 update](https://open2077.net/devblog/2026-09-25-vehicle-fleet-weapons-access) says an authority request lacking a fresh snapshot now fails safely instead of disconnecting its requester.

**Our proposal.** Capture old/new owner, epoch, latest accepted sample timestamp, readiness and rejection reason. Test stale owner packets after transfer, disconnect during transfer, and a transfer request before baseline readiness. Refuse or defer with a visible reason; never manufacture a current pose from stale state. Gate: old-epoch traffic cannot move the car; failure leaves a recoverable owner/state, not a hung passenger or silent kick.

## VEH-04: different model or appearance on the two clients

**Their result — documented interface.** [CyberMP vehicle types](https://github.com/Cyber-MP/CyberMP-Types/blob/e8625b0ea66d55fcb702e55402714d2693c9bbd5/packages/server/src/vehicles.ts) distinguish model and appearance, motion and occupants. This is a useful descriptor boundary, not a tested appearance-copy recipe for our runtime.

**Our proposal.** Store session vehicle ID separately from each local native ID, plus record, appearance and supported customization fields. Model equality alone is insufficient; position is not identity. Qualify a small explicit model set before arbitrary traffic. Gate: screenshots and descriptor logs agree before movement begins, including late join and respawn. Record unavailable appearance controls rather than substituting a different car silently.

## VEH-05: seat granted but body is outside, duplicated or in another seat

**Their result — documented contract.** [Seat switching](https://open2077.net/docs/vehicle-seat-switching) uses destination reservations and transition tokens, preserving the source occupant until completion. Cancellation can require a return presentation. This is distinct from initial boarding or a forced warp.

**Cross-project lead — closed issue.** [CyberMP Freeroam #71](https://github.com/Cyber-MP/CyberMP-Freeroam/issues/71), closed July 24, requests teleporting to the destination, waiting for loading, then spawning/mounting the car. We inspected the issue, not its implementing patch.

**Our proposal.** Treat reservation, local native parent/slot, remote visible occupant and camera as separate observations. For two simultaneous seat requests, exactly one wins. Test cancel, timeout, death and disconnect at each phase. Gate: both clients observe the intended native seats and no standing duplicate; an accepted network request alone is insufficient.

## VEH-06: ghost occupant or broken camera after teardown

**Their result — reported fix.** The [September 25 update](https://open2077.net/devblog/2026-09-25-vehicle-fleet-weapons-access) reports stale seated bodies after vehicle removal, seated-render overflow under fleet stress, and pooled workspot camera references leaking across actors. Its 200-car lab run is not a supported player count or proof of our two-client stability.

**Our proposal.** Track actor/vehicle generations in pending seat and camera callbacks. Record actual unmount completion before dropping references; cancel callbacks when the owning generation dies. Test departure while entering, while seated and immediately after vehicle removal. Verify restored camera mode and controls for both roles. Preserve the failure artifact before any recovery action.

## VEH-07: passenger ADS, shooting and window animation disagree

**Their result — versioned documented contract.** [Drive-by documentation](https://open2077.net/docs/drive-by) specifies unstable .117/protocol 1.42 for passengers and .118/protocol 1.43 for driver extensions, with matching animation assets. It describes entry/active/exit phases, sequences, native transition duration, seat checks and driver aim angles relative to the seated pose. Missing observation is separate from an observed exit. The page says stable lacks the feature; verify the installed build rather than inferring availability from a newer download number.

**Our proposal.** Defer shooting from cars until parked/moving shared seats pass. Then keep mount state, aim state, action sequence, visual phase and accepted shot separate. Test camera changes, weapon switching and exiting while aiming. Never interpret a third-person pose as proof of an authoritative hit. Do not send relative seat aim angles as world-space vehicle orientation.

## Diagnostic order for the recorded failure

Collect evidence for health/streaming, competing physics writers, stale handovers, seat lifecycle and camera ownership independently. A repair must explain which hypothesis was supported and which were rejected. Start with one parked, owned vehicle and unchanged graphics/network settings; expand only after a reproducible result. The original 01:33–01:45 video remains a symptom record, not a root-cause trace.
