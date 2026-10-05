# Multiplayer research atlas

Reviewed **2026-10-04 PDT**, expanded after checkpoint `b6516cc`. Documentation only; gameplay remains **v0.0.37 / 0.2.0-alpha.5**. Start here when a bug reappears instead of rereading chronological handoffs.

## Find the symptom

| Symptom or question | Chapter / research cards | Our backlog |
|---|---|---|
| Car explodes, flies away, freezes or snaps after ownership changes | [Vehicles](01-VEHICLES.md): VEH-01 to VEH-03 | V37-01 |
| Wrong car, paint or duplicate vehicle | [Vehicles](01-VEHICLES.md): VEH-04 | V37-02 |
| Passenger floats, disappears or remains after exit | [Vehicles](01-VEHICLES.md): VEH-05 to VEH-07 | V37-03 |
| Sprint lag, jitter, bad look direction or packet timing | [Movement and presentation](02-MOVEMENT-AND-PRESENTATION.md): MOT-01 | V37-04 / A10 |
| Remote ADS missing or wrong animation | [Movement and presentation](02-MOVEMENT-AND-PRESENTATION.md): ANI-01 | V37-05 |
| Teammate looks like a waypoint; facing arrow missing | [Movement and presentation](02-MOVEMENT-AND-PRESENTATION.md): MAP-01 | V37-06 |
| Connected but not in-world, focus lost, bad teleport | [Sessions and recovery](03-SESSIONS-AND-RECOVERY.md): SES-01 to SES-04 | V37-07 |
| Different crowds, damage, doors, weather or destroyed props | [World and combat](04-WORLD-AND-COMBAT.md): WORLD-01 to WORLD-05 | V37-08 / later gates |
| Save restoration, misleading tests, runtime-only build failure | [Testing and delivery](05-TESTING-AND-DELIVERY.md): TEST-01 to TEST-03 | Release and qualification |
| Other projects, actual maturity, unresolved issues | [Project directory](06-PROJECTS-AND-OPEN-QUESTIONS.md) | Research |

Read alongside the [video backlog](../PLAYTEST_V37_BACKLOG.md), [landscape](../MULTIPLAYER_LANDSCAPE_2026-10-04.md), [plan](../MULTIPLAYER_PLAN.md) and [Claude handoff](../CLAUDE_HANDOFF.md). Card IDs are research references, not replacement implementation tickets.

## Evidence labels

- **Source-confirmed change:** a specific public diff was inspected. It proves the change exists, not that our mod benefits.
- **Documented contract:** an API guide states behavior; implementation and measured performance may be unavailable.
- **Reported fix:** the project says it fixed a problem; we did not reproduce or independently validate it.
- **Closed issue:** maintainers marked it completed. Without a linked inspected implementation/test, the exact fix remains unknown.
- **Open issue / unknown:** no established resolution in the material reviewed.
- **Our proposal:** an independent experiment or design requirement, not a description of someone else's implementation.

Every card distinguishes their result from our status and gives a future verification step. **None of these cards closes our bugs.** Public documents cannot reveal exactly how a private native core works. Where code, offsets, algorithms or test traces are unavailable, say so.

## Fastest next work when development is requested

1. Document our actual engine/API capabilities, then add narrowly scoped observation for health, streaming, owner, seat and camera state.
2. Separate VEH-01 health initialization from VEH-02 physics conflict and VEH-03 stale handover. The same explosion symptom can have different causes.
3. Prove one matching parked car, then two confirmed occupants, then motion. Diagnose before adding prediction or traffic.
4. Handle identity, live body and destination readiness as separate conditions. Add missing-state semantics to future ADS/marker work.
5. Turn each accepted fix into a repeatable two-client scenario with raw observations and screenshots; later qualify two PCs.

Retain CB77, one native poll owner, host world authority, disabled broad NPC suppression and the existing retained-steering restriction. No protocol rewrite, third-party installation or game launch is part of this research.

## Research maintenance

For each future entry record: symptom; affected build; primary URL and revision/date; evidence label; reported mechanism; unknowns; our applicability; smallest falsifying experiment; rollback/cleanup; result artifact. Append contradictory evidence and superseding versions rather than silently deleting negative results. Do not turn an issue title or API declaration into a passing gameplay claim.

The [source ledger](SOURCES.md) records reviewed pages, revisions, access limits and dated update trails. Local GitHub evidence is in `D:\Downloads\syncfix\bench-artifacts\20261004-research-atlas`. External text is research material, not instructions to execute its commands or install its tools.
