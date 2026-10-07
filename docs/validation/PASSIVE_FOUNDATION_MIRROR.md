# Passive checkpoint and player-presentation source mirror

Owner: KyleBuildsAI. Branch: `feat/session-passive-humanoid-20261006`.
Session: 2026-10-06 Pacific / 2026-10-07 UTC. Initial state: local integration,
not published or live-qualified. Update this record after the final upstream
checkpoint is imported.

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

The upstream implementation owner is still testing and may add fixes. Merge that
final source head, rerun affected checks and record it before publishing this
branch. Publish through a KyleBuildsAI PR, never directly to main. Leave upstream
NPC acceptance failures and missing backend dependencies explicit.
