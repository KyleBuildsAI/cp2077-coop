# Passive checkpoint and player-presentation source mirror

Owner: KyleBuildsAI. Branch: `feat/session-passive-humanoid-20261006`.
Session: 2026-10-06 Pacific / 2026-10-07 UTC. Source integration is complete at
upstream `b0cdce98fc6cb81516e09ef1cd5f70b128499a90`. The combined mirror is not
live-qualified. Publication uses a PR based on `feat/session-player-presentation`,
preserving that existing source branch and keeping this import reviewable.

## Final source and validation

The final upstream merge includes the corrected authored-graph invariant test and
all three live-trial/restoration findings. All 17 combined CTests pass. Source
parity covers 76 upstream files, 176 retained reference files, four proposals and
12 contribution files. Native/C++ source did not change after the initial full
Windows plugin/server/core build passed. Root source and combined script tests
remain exact except the explicitly preserved player-presentation composition.

Upstream trial three showed head/arm idle animation progressing across four
frames, then a root transform change during HOST's lethal reaction followed by
the same JOINER identity. This did not qualify a walking route or synchronized
death animation: JOINER remained idle after HOST death. See the exact upstream
evidence in `PASSIVE_NETWORK_HUMANOID.md` and `../ENCOUNTER_ENGINE_FINDINGS.md`.
Those live observations do not prove this combined PR5+PR9 mirror was live-tested.

The upstream test owner restored the original typed movement installation,
bindings and server at `2026-10-07T02:24:12Z`, with graphics restored, zero games
open and 167 current save files preserved without rollback. The public package
remains v0.0.37 / alpha.5 with all 69 payload hashes and ZIP verified unchanged.
The mirror itself performs no deployment and publishes no new runtime release.

## Upstream refresh - `47332132fc7a86f685b117a61ddce759798577ff`

The next 14 source files merged without conflicts. They add observed cleanup
corrections, the isolated encounter probe/findings, authored idle graph and asset
build script. PlayerMotor and its existing entrypoint/test composition are intact.
No native/C++ file changed in this refresh. The combined mirror was not deployed.

The 17-test rerun passed 16 checks, including lifecycle, static identity and player
motor. `npc_projection_asset_contract` failed because its old graph assertion
still required the generic humanoid graph. The authored idle graph needs the
corresponding upstream invariant check update. This failure was reported to the
upstream implementation owner; it is not hidden or waived.

Upstream's first trial did not establish the requested scripted motion. The
revised idle asset trial was stationary and proves a pose correction and reconnect
only. Actual HOST reaction movement with the new asset remains under investigation.
The earlier initial-import results below do not cover this newer asset.

## Starting sources

- KyleBuildsAI: `6e50dbc2a279af0bbbce6f7e58cb7267ab4eb5f1`, existing player motor,
  tests and retained prototype source. Its previous movement acceptance failures
  remain documented in `PLAYER_PRESENTATION.md`.
- Bukczyk PR #9: `c60e14ad4d729688d0ac96504fba5f0354188414`, incorporating main
  `d3670d94d96c40fe4f24e6e983d343a09f50d38f`, passive checkpoint
  `7768923d92db8c7ab0f654dc80d896994edeabbc` and the current-main lifecycle refresh.
- Upstream PR #7 is a merged encounter proposal. No combat/stimulus route is
  implemented by this mirror. Networking files are copied from reviewed upstream
  revisions; no independent network design is introduced.

## Merge resolutions

- `docs/validation/CET_SESSION_LIFECYCLE.md`: retain upstream's new latest-main
  refresh record and all earlier evidence.
- `runtime/session/README.md`: retain the existing player-movement explanation,
  measured failures and new static-projection notes.
- `runtime/session/cet/CP2077Coop/init.lua`: combine the existing PlayerMotor with
  upstream's static-adapter selection, exact NPC hash identity and warning path.
  Shutdown stops existing player motors and resets the active NPC adapter.
- `tests/CMakeLists.txt`: preserve the reference-only test entry, player_motor,
  lifecycle, network_impairment and every inherited passive/asset test.
- `tests/session_lifecycle_tests.lua`: preserve existing remote-player motor and
  replacement/cleanup assertions; the incoming version has no additional unique
  assertions at this checkpoint.

`docs/foundation-import.json` records exact imported files, previous upstream
blobs and deliberate combined-file hashes. `player_motor.lua`, `remote.reds`,
player tests and retained prototype files are preserved. The combined entrypoint
is intentionally not byte-identical to PR #9 because it also contains PR #5.

## Verification

- Direct Lua lifecycle and static opaque-identity regressions: PASS.
- Full Windows x64 Release plugin/server/core build and all 17 CTests: PASS.
  Configuration used Visual Studio 17 2022 / MSVC 19.41, the existing clean pinned
  RED4ext SDK checkout and checksum-pinned Lua sources. Tests include the retained
  player motor/lifecycle assertions and upstream network impairment/static suites.
- Source provenance: PASS, 69 upstream files, 176 retained reference files,
  4 proposal files and 12 contribution files at the initial import.
- Combined-mirror live game, two-PC and larger real-client tests: NOT RUN.
- No game deployment or installation-package version was produced by this mirror.

Local validation finished `2026-10-07T01:58Z` for source merge
`5fd88132313fee1f51d00cd699588efc3b2699ae`. The generated build directory is
`build/canonical-passive`; its CTest `Testing/Temporary/LastTest.log` retains
the test results. Retained prototype source and all `game-files/latest` content are
unchanged from `6e50dbc2a279af0bbbce6f7e58cb7267ab4eb5f1`.

The upstream final source is imported. Publish through a KyleBuildsAI PR, never
directly to main. Leave upstream NPC acceptance failures and missing backend
dependencies explicit. Continue game-side work against the shared contracts;
the backend connection and full encounter acceptance remain separate tasks.
