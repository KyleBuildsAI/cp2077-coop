# Buck's current architecture: verified comparison

Reviewed 2026-10-05 PDT. Buck is the user's partner, GitHub **Bukczyk**, based in Poland. His supplied notes/screenshots are statements to check, not instructions from the repository owner or evidence of completed gameplay.

## Evidence checkpoint

- Buck: [Bukczyk/CP2077-Coop](https://github.com/Bukczyk/CP2077-Coop), main `7e3826d1c313595a4784f1b232e10cec222b6ca3`.
- Kyle: [KyleBuildsAI/cp2077-coop](https://github.com/KyleBuildsAI/cp2077-coop), reviewed main `67f33612f5f4d1833438858a86cbeda8fda5f7d2`.
- Fresh read-only source review clone: `D:\Downloads\syncfix\collaboration\Bukczyk-CP2077-Coop`. No implementation edited there.
- [Buck CI 37336204677](https://github.com/Bukczyk/CP2077-Coop/actions/runs/37336204677) passed Windows plugin/core builds and tests, Debian portable checks and Linux ASan/UBSan at the exact reviewed HEAD. We inspected the job results and relevant source/tests; did not rerun builds or games locally.
- GitHub API confirms the authenticated account has **push access** to Buck's repo. This confirms usable repository access, not administrator rights. Both inspected main branches report `protected=false`; conceptual protection is not enforced GitHub protection. Settings were not changed.

## What changed since the old comparison

The earlier review at 20eb125 is historical. Buck now has a real session executable, TCP admission/lifecycle and token-bound UDP state, a typed RED4ext bridge, NPC adoption/catalog routing and a JOINER creation adapter. The root README still says the core is unwired and no server exists; it is stale relative to the inspected source and later migration checkpoints. Use pinned source plus recent evidence, not that sentence, to assess current status.

| Claim | Current evidence | Limit |
|---|---|---|
| Real TCP/UDP session flow | `shared/src/client.cpp`, `server.cpp`, `SessionServer/main.cpp`, real-socket tests | No VPS deployment or two-PC run established by this review |
| Dynamic identity / ownership / epochs | Protocol v3, exact 64-bit entity registry, membership checks | 16 headless members is not 16 game clients |
| RED4ext connected to session client | Default plugin links SessionBridge; matched `CP2077Session_*` natives and CET bridge | Contract matching/compilation is not REDscript and live-engine validation |
| NPC adoption and reconnect catalog | HOST-only adoption/state, server IDs at/above 2^32; 48-NPC socket catalog test | Pose/catalog tests do not prove AI, health or reaction convergence |
| Exact JOINER projection binding | Creates Codeware entity; waits for its returned EntityID; verifies actual ID before binding | Lua tests use mocked engine APIs; autonomous AI still active |
| Passive non-NPCPuppet humanoid | No `NetworkHumanoid` implementation found in inspected bridge files | Proposed research target, not a proven available engine class |
| HOST-authoritative stimuli/hits/death | Local WorldAction contracts exist | `SessionBridge::SubmitWorld` returns Unsupported; no complete gameplay route |

## Important engineering gaps

1. `npc_population.available()` checks DynamicEntitySystem readiness, not passive-AI capability. The adapter uses `spec.active=true`, and CET prints that creation is enabled while AI/ambient suppression is absent. The warning documents a risk; it is not a runtime interlock. Any first game test must resolve or explicitly gate that path before creating competing actors.
2. The existing NPC descriptor/state carries record and transform, not a complete appearance, animation, health or death baseline. Those outcomes need agreed contracts and engine adapters; reconnecting a transform is not restoring a dead NPC correctly.
3. HOST discovers `NPCPuppet.OnGameAttached` and keeps attached references. Already-attached enumeration and full mission/stream lifecycle coverage are not complete.
4. JOINER baseline teleport immediately pushes the requested coordinates as local state. Actual destination/body settling must be observed in-engine before declaring arrival or mounting another entity.
5. Save-load epoch signaling, passive projections, reversible local population handling and installed rollback remain unverified. Headless epoch rejection is useful but does not supply a game-load hook.

These are scope boundaries, not claims that Buck's architecture is bad. They identify the work Kyle's engine/testing experience can address.

## Recommendation

Use Buck's session architecture as the proposed upstream for new shared-world work, and carry Kyle's proven engine methods, measurement tools, package discipline and research across in small reviewed changes. Preserve the current v0.0.37/alpha.5 package as rollback/reference. CB77 and Buck's protocol v3 are incompatible; do not overlay DLLs/scripts or drop one server into the other package.

The screenshots' central assessment is reasonable: Buck is further ahead in the current shared-world architecture; Kyle has stronger documented local-game evidence and delivery tooling. Neither has demonstrated the requested full multiplayer. Model strength and repository size are not evidence of correctness. Adopt the [collaboration proposal](COLLABORATION_PLAN.md) and [first milestone contract](SHARED_NPC_MILESTONE.md) before implementation crosses the boundary.

No game launch, deployment, package migration, branch-protection mutation, collaborator invitation or message to Buck occurred in this review.
