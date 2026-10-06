# Shared development instructions

## Collaboration review, 2026-10-05

Read `docs/COLLABORATION_PLAN.md`, `docs/BUCK_REVIEW_2026-10-05.md` and
`docs/SHARED_NPC_MILESTONE.md` before new integration work. Use task branches and
PRs; do not push directly to main or merge cross-owner changes without the other
maintainer's review. Record task owner, exact paths, base SHA and UTC handoff.
This repository remains the existing runtime/reference source until a migration
is mutually agreed. Buck's repository is the recommended future upstream, not an
already approved replacement. Permission to push there is not task ownership.
Keep both protocols/packages separate until a matched cutover is verified.

## Canonical project

This repository is the shared source of truth. The gameplay scripts live in `bin/`
and `r6/`; `plugin/` contains CP2077CoopNet, `relay/` the current CB77 relay,
`npcsync/` the isolated NPC harness, and `phase1/` plus `phase1-check/` the measurement
tools. Their original histories were imported as Git subtrees. The old standalone
checkouts are historical references; make new changes in this repository.

Read `docs/DEVELOPMENT.md`, `docs/INTEGRATION_PROVENANCE.md` and
`docs/MULTIPLAYER_PLAN.md` before changing cross-component behavior. Preserve user
edits and test evidence. Work in a feature branch or separate worktree, coordinate
file ownership with other agents, and keep commits reviewable. Do not infer that a
second contributor has approved a change just because an AI review passed.

## Protocol and authority

Keep the working CB77 v2.1 transport and game-facing API compatible. The friend's
CPS1 protocol is a separate design, not a drop-in replacement. There must be one
owner of native polling. New experimental channels need explicit allocation,
bounded queues, session/generation checks and separate lifecycle cleanup.

HOST owns world identity and accepted gameplay results. JOINER sends intent.
The relay authenticates membership and enforces routing/policy; it does not run
Cyberpunk's simulation. A seat grant is not proof of an engine mount, and a spawn
or delete request is not proof that an entity exists or is gone. Observe the
actual game state before acknowledging those operations.

Never use coordinates as identity or encode new events in movement float sentinels.
Keep old epochs, disconnected memberships and duplicate requests from changing
current state. Validate data before mutation. Preserve explicit rejection and
overload behavior instead of reporting a successful action that was not applied.

## Validation and reporting

### Standing release instruction from Kyle (2026-10-04)

For every new mod/runtime or installation-package version, update GitHub **and**
refresh `game-files/latest/`, including its complete local `game-root/`, matched
relay, ZIP, manifest, checksums and instructions. Publish the matching versioned
GitHub release assets after verification; pushing source alone does not complete
a version. This is standing user authorization, so do not ask for the same routine
push/package/publication permission again. Follow `docs/RELEASE_WORKFLOW.md` and
update the Obsidian handoff/status with the version, commit, links and results.
Keep prior releases recoverable and do not publish failed builds as verified.
Documentation-only edits do not require a new gameplay version or repackaging an
unchanged, verified runtime.

Run the checks appropriate to changed components. Portable C++ and Python tests
belong in CI. `tests/run_all.ps1` also compiles redscript using a read-only local
game reference; `-SkipRedscript` is only the explicit game-free CI subset.
The known A10 sprint-lag assertion is a tracked failure, not a passing assertion.

Separate evidence levels: codec/unit checks, actual transport integration,
compiled game bridge, local two-game test, and two-PC internet test. Do not promote
one level into another. Record commit, configuration, duration and limitations.
Archive negative results as well as successful ones.

Use only the authorized test installations for deployments, with both games
closed. Preserve saves and settings before live tests and restore them afterward.
Close only test-owned programs. The broad NPC population-suppression prototype
must remain disabled; controlled owned actors are the current supported experiment.

Keep the repository roadmap and the Obsidian handoff in `G:\CyberpunkMP` aligned
with completed work. Do not call complete multiplayer achieved while player,
vehicle, NPC, combat, world and persistence acceptance gates remain unmet.
