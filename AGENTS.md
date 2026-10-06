# Shared development instructions

## Direction and repositories

Build large multiplayer co-op with dynamic player groups. Two-client smoke tests
are a starting test case, not a product limit. Headless member counts do not prove
live game capacity; do not promise unlimited players.

`Bukczyk/CP2077-Coop` is the collaboration upstream. KyleBuildsAI authorized
integrating its foundation into `KyleBuildsAI/cp2077-coop` so compatible game-side
work can start here. Preserve the imported typed contracts and root paths. Do not
create a competing network design. Track the exact upstream revision and local
changes in `docs/FOUNDATION_INTEGRATION.md`.

Read `docs/DEVELOPMENT.md`, `docs/FOUNDATION_INTEGRATION.md` and the current section
of `docs/MULTIPLAYER_PLAN.md` before implementation. `ARCHITECTURE.md` and
`MIGRATION_PLAN.md` preserve upstream design and checkpoint history; early status
paragraphs may describe older revisions. The archived CB77 roadmap is historical,
not permission to continue a second foundation.

Use task branches and pull requests. Record exact file ownership and UTC handoffs;
do not push directly to main. Bukczyk owns network/session/server contracts;
KyleBuildsAI owns game integration, presentation, map/UI, vehicle/seat engine hooks
and live measurements. Preserve unrelated edits, authorship and test evidence.
Proposals in Bukczyk PRs #1, #2 and #3 remain proposals until Bukczyk accepts them.
An AI review or a merge in KyleBuildsAI's repository is not that acceptance.

Questions and explanations authorize discussion and read-only inspection only.
Edit, commit, push or create PRs only for explicitly requested implementation.
Routine release permission applies only to that requested implementation.

## Source and build boundaries

- Active foundation: `shared/`, `SessionServer/`, `CoopPlugin/`, `runtime/session/`.
- Retained reference implementation: `bin/`, `r6/`, `plugin/`, `relay/`, `npcsync/`.
- Retained measurements: `phase1/`, `phase1-check/`, `coop-tools/` and their tests.
- Frozen upstream legacy import: `runtime/cet/`, `runtime/redscript/`,
  `runtime/tweaks/`, `CoopServer/` and `CoopPlugin/src/legacy_main.cpp`.

Default root CMake and `scripts/build.ps1` / `scripts/build.sh` use the typed
foundation. `scripts/build-reference.ps1` / `scripts/build-reference.sh` select
`COOP_REFERENCE_ONLY=ON` for preserved CB77 checks. Use separate build directories.
The native plugins use different pinned SDKs; never configure both in one tree.
Do not alter vendor code to hide build failures or deploy mixed runtime stacks.

## Protocol, authority and engine rules

Keep protocol/session code portable and separate from engine integration. Use
explicit packet types, SessionId, PlayerId, SessionEntityId and world epochs.
Preserve opaque 64-bit identities through Lua. Coordinates and local engine
EntityIDs are not session identity. Never encode new actions in movement floats,
extend CP1/RP1 sentinels or copy the CB77 wire protocol into the typed bridge.

HOST owns world simulation and accepted gameplay outcomes. JOINER sends intent.
The server validates membership, roles, routing and accepted state; it does not
run Cyberpunk's simulation. Encode bytes explicitly, never native struct memory.
Validate lengths, enums, finite values, ownership, sequence, epoch and capacity.
Old connections, duplicate requests and stale generations must not mutate current
state. Reject or reconcile overload rather than claiming an unapplied success.

No engine object access from network threads. Deliver coherent snapshots and
bounded events to the game thread. Observe actual spawn placement, mounting,
damage and disappearance before acknowledging completion. A seat grant is not an
engine mount, and DeleteEntity returning true is not proof of disappearance.

The current JOINER NPC adapter is not proven passive. A normal NPC with one
component disabled is not proof that autonomous AI is stopped. Keep broad ambient
population suppression disabled and use controlled owned actors for experiments.

## Validation and release

Run checks appropriate to changed components. Protocol/session changes require
malformed-input, authority, isolation, ordering, disconnect and resource-bound
tests. Documentation-only changes require review and `git diff --check`.
Record PASS, FAIL and SKIP honestly. Reference `tests/run_all.ps1` compiles legacy
redscript against a read-only game reference; `-SkipRedscript` explicitly omits
that check. Its known A10 sprint-lag assertion remains a visible waiver.

Separate portable tests, real-socket tests, native compilation, script compilation,
two-game smoke tests and multi-PC group qualification. Record revision, topology,
configuration, duration and limitations. Source alignment does not certify a
playable cutover. Keep the tested v0.0.37 / alpha.5 package until a matched typed
replacement passes its gates; do not silently install newly built DLLs over it.

Standing instruction from KyleBuildsAI (2026-10-04): every requested new runtime or
package version must update GitHub and the complete local `game-files/latest/`,
including matched components, manifest, verified hashes and release assets.
Follow `docs/RELEASE_WORKFLOW.md` and update `G:\CyberpunkMP` status/handoff/resume.
Preserve earlier releases. Source integration that publishes no runtime/package
version does not require repackaging unchanged, verified bytes.

Deploy only to authorized test installations with games closed. Preserve saves
and settings, restore them after tests, and close only test-owned processes.
Keep the roadmap and vault aligned with observed evidence. Do not claim complete
multiplayer while advertised player, vehicle, NPC, combat or persistence gates
remain open.