# Large co-op foundation integration

## Connected diagnostic source preparation - 2026-10-08 UTC

Local branch `work/canonical-connected-combat-20261008` merges upstream candidate
`755ab0cf883ad80b519060814ff45f43f6d25714` into Kyle PR #6's `2f29c27` checkpoint.
It then imports the GCC 12 formatting correction
`aa289044907a273a34a6819dafae36a21fc50378` through a second merge.
The next source import, `a8b98c72d426e62e8e2df1ddcad35a402eb8e1bc`, adds controlled
HOST ray rejection diagnostics only. Existing ray acceptance and damage behavior
remain unchanged; live trial results belong to the test owner's later record.
The source candidate is from `work/connected-combat-20261007`, prepared for draft
Bukczyk PR #10. It includes PR #11's merged generic gameplay routing and updated
PR #9 at `5526210f669310ae4ba20ad58b8bb8712fd272fc`. PR #9 remains pending;
this preparation does not claim that its engine changes have reached main.
Kyle PR #6 remains draft and unchanged until this isolated integration is handed
back for publication. Neither main is modified.

The merge preserves both histories, Kyle's player motor and all retained reference
files. Its only conflict was the test list: the reference-only entry, player motor
and every upstream suite remain registered. The generic bridge now exposes opaque
reliable gameplay requests/results to scripts. The default-off `CPEX1` diagnostic
is an experimental proposal, not agreed production combat. It applies the HOST's
current weapon through a labelled synthetic fixture and reports observed health
and life state, without claiming remote weapon parity or attributed damage.

No connected live pass or combined-mirror live test is claimed. The public
**v0.0.37 / alpha.5** package and release assets remain unchanged. Exact provenance,
owned files, validation and next steps are in
[the connected mirror record](validation/CONNECTED_ENCOUNTER_MIRROR.md).
Earlier sections below retain their historical evidence and limitations.

## Physical encounter mirror - 2026-10-07 UTC

The `feat/session-passive-encounter-20261007` branch imports upstream implementation
`63b70987c249860097bfc460a3406fcea5bf2837` and final evidence documentation
`3aecdc114aee3762f812635bf5ad07793ac3d571` from
[draft Bukczyk PR #10](https://github.com/Bukczyk/CP2077-Coop/pull/10), stacked on
the previous passive checkpoint and KyleBuildsAI PR #5. Both histories remain.
The merge preserves existing player presentation, every retained reference file,
the reference-only CMake entry and the player motor test. Only the test-list merge
needed conflict resolution; both branches' tests remain registered.

The new source provides exact local firing/query candidates, local reaction/death
presentation and safe pending-retirement handling. It does not implement shared
combat transport: `SessionBridge::SubmitWorld` still returns `Unsupported`.
The combined source has its own offline validation; upstream live evidence must
not be described as a live test of this combined mirror. Final upstream evidence documents native HOST health loss/death and the corrected
local presentation trial. All 20 combined local tests pass; the three-file final
upstream refresh is documentation only. See [the current mirror record](validation/PHYSICAL_ENCOUNTER_MIRROR.md).
No game installation or v0.0.37 / alpha.5 package changes are part of this mirror.

## Passive checkpoint mirror - 2026-10-07 UTC

The `feat/session-passive-humanoid-20261006` branch combines upstream
`b0cdce98fc6cb81516e09ef1cd5f70b128499a90` from
[Bukczyk PR #9](https://github.com/Bukczyk/CP2077-Coop/pull/9) with the existing
KyleBuildsAI player-presentation checkpoint `6e50dbc2a279af0bbbce6f7e58cb7267ab4eb5f1`.
Both source histories and the retained reference implementation are preserved.
This is an isolated source integration; the primary checkout, game installations
and public `game-files/latest` are not changed by it. Current merge differences,
test results and pending upstream follow-up are recorded in
[the mirror validation note](validation/PASSIVE_FOUNDATION_MIRROR.md).

The static NPC adapter matches the pinned upstream contribution. The combined
entrypoint also retains PR #5's per-player movement motor. This composition needs
its own qualification; a live result from either input branch is not proof that
the combined mirror was live-tested. The v0.0.37 / alpha.5 package remains the
tested public reference. No new runtime release is claimed.

## Current player-presentation work - 2026-10-06T06:48:16+00:00

KyleBuildsAI owns the game-side movement task requested after the regression was
seen in the typed preview. [Draft Bukczyk PR #5](https://github.com/Bukczyk/CP2077-Coop/pull/5)
is stacked on lifecycle PR #4; upstream head `e61456b1b4d78b973721e485767eeacf928b2573`.
Canonical branch: `feat/session-player-presentation`. Both main branches remain
unchanged. See [exact files and test results](validation/PLAYER_PRESENTATION.md).

The previous per-frame NPC transform call left the body frozen in the measured
route. The new per-player motor restores engine movement. P95 tracking error fell
from 10.191 m to 4.861 m forward; reverse measured 5.355 m. This is still a failed
smooth-tracking gate, not a completed multiplayer port. Weapons/crouch/ADS are
not networked. Unqualified local action hooks live only in `experiments/`.

All 13 local CTests pass and final movement scripts compile against the installed
game/Codeware in isolation. Both games/server are closed; graphics and bindings
are restored byte for byte; current saves were retained without rollback. The
test copies retain the movement candidate. The larger-window shortcut still
works for future tests. Evidence: `D:/Downloads/syncfix/bench-artifacts/20261006-player-presentation`.

Hosted Windows, Debian and ASan/UBSan checks passed at exact upstream head `e61456b1b4d78b973721e485767eeacf928b2573`: [CI run 37425748668](https://github.com/Bukczyk/CP2077-Coop/actions/runs/37425748668). Current manifest verification passed: 59 unchanged upstream, 176 preserved reference, 4 proposal and 12 contribution files.

Next: measure and reduce engine path/turn lag, qualify actual weapon/posture
effects, and review `docs/PLAYER_PRESENTATION_CONTRACT.md` with Bukczyk for the
missing shared state route. Do not change protocol/server or world-NPC adapters
under this task. Keep `game-files/latest` v0.0.37 / alpha.5 until a complete matched
replacement passes live gates. This is source research, not a new version release.

KyleBuildsAI requested the session foundation from Bukczyk's repository in this
repository so game-side development can start against the same interfaces.
The goal is **large multiplayer co-op with dynamic player groups**, not a permanent
two-player architecture.

## Previous lifecycle correction

[Bukczyk PR #4](https://github.com/Bukczyk/CP2077-Coop/pull/4) fixes CET startup
and repeated player-session resets. Upstream review head:
`9b0d7065dc4e853ddc32b6b221330e412b403561`. The local mirror is on
`fix/cet-session-lifecycle-local`, not main. The contribution is proposed and
tested, not accepted by Bukczyk or released as a new game package.

The manifest now preserves 60 unchanged upstream blobs, 176 reference files and
four proposal documents, plus five exact contribution files. It retains original
blob IDs and PR provenance for the two deliberately changed upstream files.
`tests/CMakeLists.txt` also includes the new regression while retaining the local
reference-build prefix. The verifier checks all four groups.

See [the test record](validation/CET_SESSION_LIFECYCLE.md): fresh Windows build,
12/12 CTests, LuaJIT precision/lifecycle regression and two-game startup/reconnect
evidence. Movement remains unresolved. The public v0.0.37 / alpha.5 package stays
the tested reference while this focused source correction awaits upstream review.

## What is integrated

Upstream source: [Bukczyk/CP2077-Coop at 7e3826d1c313595a4784f1b232e10cec222b6ca3](https://github.com/Bukczyk/CP2077-Coop/tree/7e3826d1c313595a4784f1b232e10cec222b6ca3).
KyleBuildsAI starting revision: `67f33612f5f4d1833438858a86cbeda8fda5f7d2`.
The import retains both Git histories through a merge, preserving authorship and
an upstream merge base for later updates.

| Area | Current source |
| --- | --- |
| Typed protocol, sessions, ownership and identities | `shared/` |
| TCP/UDP session server and Debian deployment inputs | `SessionServer/`, `deploy/debian/` |
| RED4ext session integration | `CoopPlugin/` |
| Dynamic player bridge and NPC projection foundation | `runtime/session/` |
| Portable, real-socket and NPC lifecycle checks | Upstream C++/Lua tests in `tests/` |
| Matched developer profiles | `scripts/package-session.ps1` |

The [import manifest](foundation-import.json) originally recorded 62 unchanged
upstream files, 176 retained prototype source/test files and four proposal
documents. The current correction and its explicitly tracked differences are
described above. Run:

```powershell
python scripts/verify-foundation.py
```

This verifies source equality, not gameplay. An intentional later port must update
the comparison record explicitly and explain the change in its PR. Do not change
a hash just to hide accidental divergence.

The six upstream files adapted here are `.gitattributes`, `.gitignore`,
`AGENTS.md`, `README.md`, root `CMakeLists.txt` and `tests/CMakeLists.txt`.
Build configuration selects the typed foundation by default and keeps old checks
in an isolated reference mode. Runtime files, portable networking, native session
code and upstream test implementations remain unchanged at import.

## Preserve the existing work

KyleBuildsAI's `bin/`, `r6/`, `plugin/`, `relay/`, `npcsync/`, measurements and tests
remain available with their history. Markers, player recovery, NPC safeguards,
presentation and vehicle experiments still need individual ports to the typed
session bridge. Their presence here does not mean the active session runtime uses
them already.

[Bukczyk PR #1](https://github.com/Bukczyk/CP2077-Coop/pull/1),
[PR #2](https://github.com/Bukczyk/CP2077-Coop/pull/2) and
[PR #3](https://github.com/Bukczyk/CP2077-Coop/pull/3) contain collaboration and reuse
plans. Their four documents are copied here without changing their proposal status.
Those PRs are not feature implementations, and a merge here does not approve them
for Bukczyk. This working repository contains additional reference code and build
integration, so the whole repositories are not identical even though the imported
foundation is aligned.

## Build now

See [development instructions](DEVELOPMENT.md) for the usual build scripts.
Use fresh, separate build directories for the two SDK versions:

```powershell
# Typed session foundation, including the native plugin and session server.
cmake -S . -B build/foundation-20261005 -A x64 -DCOOP_BUILD_PLUGIN=ON -DCOOP_BUILD_LEGACY_SERVER=OFF
cmake --build build/foundation-20261005 --config Release --parallel
ctest --test-dir build/foundation-20261005 -C Release --output-on-failure --no-tests=error

# Preserved CB77 plugin, portable checks and real transport loopbacks.
./scripts/build-reference.ps1 -NativePlugin -BuildDirectory build/reference-integration-20261005

# Prepare private matched development profiles after the typed build passes.
./scripts/package-session.ps1 -BuildDirectory build/foundation-20261005
```

Generated HOST/JOINER profiles contain a private test key under ignored
`artifacts/`. Do not commit or publish that key. The package script does not install
anything. Its Debian directory contains deployment inputs, not a Linux executable.
Build the server on Linux using `scripts/build.sh`.

The Windows session server is
`build/foundation-20261005/SessionServer/Release/CP2077SessionServer.exe` and the
typed plugin is `build/foundation-20261005/CoopPlugin/Release/CP2077Coop.dll`.
The retained reference DLL is built in the other tree and must not be overlaid.

## Capacity and current limits

The imported server default is 16 members. Its current configuration parser allows
an explicit `max_players` value up to 256; that is a configuration bound, not a
claim of 256-player gameplay. The inherited headless test exercises 16 members.
Larger live groups need measured engine, network and world-simulation qualification.

Two game clients remain a useful first smoke test. New code must use collections
keyed by player/entity ID and work toward larger groups instead of adding a new
single-partner assumption.

NPC projection creation and reconnect identity are implemented in the foundation.
Passive JOINER AI, complete appearance, authoritative reactions/hits/death,
shared seats/physics, campaign state and general world persistence remain open.
Read the later checkpoints in [runtime/session/README.md](../runtime/session/README.md);
early upstream README/migration statements are historical. No game launch or
installation is part of this source integration.

## Verification record

- Import parity: 62 upstream files, 176 preserved reference files and four proposal documents checked.
- Retained reference: Windows native build and all 15 CTests passed, including DLL load and four real UDP profiles.
- Typed foundation: Windows native plugin and session server built; all 11 inherited CTests passed, including the 16-member session scenario, NPC catalog/reconnect, Lua projection lifecycle and runtime/native contract checks.
- Imported REDscript: `natives.reds` and `remote.reds` compiled successfully with Codeware in an isolated sandbox at `2026-10-06T04:28:34Z`. Compiler, game cache and Codeware inputs were copied from a read-only test installation; input hashes were unchanged.
- Hosted Windows/Linux/sanitizer checks: see the integration PR for exact-commit results.
- Live game and larger real-client groups: not run by this source integration.

Local evidence is under
`D:\Downloads\syncfix\bench-artifacts\20261005-foundation-integration`.
Source parity and offline passes do not prove shared-world gameplay.

## Package and next task

The last game-tested complete package remains **v0.0.37 / alpha.5** in
`game-files/latest`. This source import creates no new gameplay version and does
not replace that package with an unqualified foundation build. The public ZIP and
its hashes remain the rollback/reference. Every later runtime/package release
still follows [the release workflow](RELEASE_WORKFLOW.md).

Start new game-side work in `runtime/session/`, on a new task branch, using the
imported calls. The first joint milestone remains a stable, safely controlled NPC
and then HOST-owned reactions to JOINER actions. Coordinate existing NPC-adapter
work before editing the same files. Bukczyk owns shared networking contracts;
KyleBuildsAI owns engine integration and presentation. Keep later contributions
small and compatible so they can be reviewed upstream without a second rewrite.
