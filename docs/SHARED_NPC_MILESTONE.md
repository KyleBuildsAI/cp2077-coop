# Shared NPC reaction milestone: draft contract and acceptance

Prepared 2026-10-05 against Buck `7e3826d`. **Contract proposal, not implemented wire fields or agreed reservations.** Read [collaboration rules](COLLABORATION_PLAN.md) and [current evidence](BUCK_REVIEW_2026-10-05.md).

## Objective and sequence

HOST and JOINER observe one stable NPC. JOINER produces one supported stimulus or hit intent; HOST decides AI/damage; both present the accepted reaction/death. First use an explicitly controlled owned test actor, not arbitrary mission population. A passing owned-actor test does not certify general world/campaign support.

1. Compile the matched script/native bridge in isolation and prepare verified rollback.
2. Prove exact projection identity, passive behavior, movement and cleanup with no combat.
3. Agree/implement the reliable stimulus/hit/result routes and a canonical life baseline.
4. Qualify one stimulus, then one nonlethal hit, then death and reconnect.
5. Repeat on two PCs before describing internet co-op as qualified.

Failure at one stage blocks dependent stages. Stimuli cannot compensate for an unsafe JOINER projection.

## Existing boundary to preserve

Buck uses SessionId u64, epoch u32, PlayerId u32 and SessionEntityId u64. Exact local engine IDs are not session IDs or pointers. NPC IDs start at 2^32; numeric reuse across epochs means the full identity includes session and epoch. Transforms use Euler radians on the wire; the current CET facility converts yaw to degrees. Preserve opaque 64-bit identity across Lua.

Reliable NPC adoption/catalog/removal currently travels by TCP, sequenced poses by UDP. Pose/catalog state lacks complete appearance, animation, health and death reconstruction. General WorldAction submission is Unsupported in the inspected bridge. Existing HitRequest/DamageApplied declarations do not establish a complete live SessionClient/server/game route.

## Draft interface requirements to agree before coding both ends

| Operation | Producer -> consumer | Required semantics |
|---|---|---|
| Projection descriptor / baseline | Accepted server catalog -> JOINER adapter | Stable identity, record and supported appearance; descriptor/life revision; explicit live/dead state and supported presentation state; capability/asset mismatch rejection |
| Create/bind/delete completion | Kyle engine adapter -> bridge | Exact returned local ID; pending/ready/failed; observe creation and disappearance; never acknowledge a request merely because it was queued |
| Stimulus intent | Admitted JOINER -> HOST via server | Request identity, session/epoch, source, supported stimulus kind, finite origin/direction or position; bounds and current actor/life checks |
| Hit intent | Admitted JOINER -> HOST via server | Request/shot correlation, stable attacker/target, weapon/action context and observations; proposed damage is untrusted; no nearest-coordinate target selection |
| Accepted reaction/damage/death | HOST -> server -> peers | Canonical event/revision and causal request identity; supported reaction; authoritative resulting health/life; apply once; explicit refusal/unsupported/expired outcomes |
| Reconnect / restream | Server catalog -> adapter | Current life/presentation baseline at a defined boundary; old generations/events cannot resurrect or damage the replacement entity |

Buck owns byte layouts, packet allocation, version negotiation, reliable ordering, capacity and routing. Kyle owns actual engine read/apply/completion APIs and their thread/lifecycle constraints. Both sign off units, clock domains, correlation scope, life revisions, timeout/overflow behavior and compatibility fixtures. Do not invent new message IDs inside a Lua probe or encode actions in movement fields.

## ENG-01: passive projection research

`NetworkHumanoid` is a candidate name from Buck's proposal, not an established class in the reviewed implementation. Success is a verified presentation entity that is visible and movable while running no independent AI/damage decisions; a particular class name is not the gate.

Research two bounded candidates: a verified non-NPC presentation entity, or a normal puppet with verified scoped AI/decision suppression. Verify record/rig/animation compatibility and whether suppression also breaks animation, attachment, hit detection or movement. Never treat `DynamicEntitySpec.active=false`, freezing transforms or removing a single AI component as a proven solution without observation.

The production adapter must report capability unavailable until the required hooks are verified, and spawn nothing on failed capability. Preserve ambient actors and original state. Broad crowd deletion/suppression stays disabled. If local duplicates require population handling, use one bounded reversible test area and a separate acceptance gate before broadening it.

## Acceptance record

All timings and tolerances below are proposed test gates to be agreed, not observed results. Capture both roles with commit/package/server versions, UTC/monotonic timestamps, exact identities, actions, native outcomes, logs and video.

| Check | Pass evidence / failure criterion |
|---|---|
| Identity | One source maps to one projection; two entities at identical coordinates stay distinct; asynchronous binding never substitutes a nearby NPC |
| Passive idle | At least 60 seconds visible with no stimuli: JOINER projection issues no independent AI tasks, damage or movement decisions; HOST source still behaves normally |
| Movement | HOST pose changes move the same JOINER entity; presentation tolerance and measurement method agreed before the run; no spawn-per-frame or origin flashing |
| Stimulus | One JOINER action reaches HOST once and causes the supported HOST reaction; JOINER shows that accepted reaction, with no independent local decision |
| Nonlethal hit | HOST validates one supported hit; both report the same resulting life/health revision; duplicate delivery does not apply damage again |
| Death | One accepted lethal result produces persistent dead state on both sides; no locally autonomous resurrection |
| Reconnect/restream | Live and dead cases rebuild exact session identity/current life; no ghost body or replayed old damage |
| Cleanup | Disconnect, bubble exit, actor removal, failure and epoch reset clean up owned projections and restore prior local state; failed delete remains pending/visible in logs |
| Rejections | Foreign epoch, invalid target, unauthorized world result, duplicate/late request and queue overflow fail explicitly without mutation |

The first live runner must verify process/settings/save state afresh. October 4 restoration records are historical. Preserve new user progress, back up test state, deploy only to authorized Baseline/Test B with games closed, and never mix CB77/alpha.5 files with Buck's session DLL/native declarations. No live test or installation is performed by this document.
