# Integration provenance

## Typed large co-op foundation, 2026-10-05

KyleBuildsAI explicitly requested integrating Bukczyk's session foundation into
this repository for compatible game-side development. The merge retains upstream
history at `7e3826d1c313595a4784f1b232e10cec222b6ca3` and the existing source at
`67f33612f5f4d1833438858a86cbeda8fda5f7d2`. See
[the import record](FOUNDATION_INTEGRATION.md) and
[file-by-file baseline](foundation-import.json). Unlike the earlier design-only
adoption below, this imports the actual typed networking, server, plugin, runtime
and tests at their upstream paths. Six root/build/instruction files are adapted;
62 other upstream files and 176 prototype source/test files are preserved.

The active direction is dynamic large-group co-op. The old CB77 runtime remains
a reference for individual feature ports and rollback; no matching gameplay
cutover or new live capacity is claimed by this source merge.

## Earlier prototype integration history

The shared repository preserves the working v0.0.37 gameplay baseline at
`af1f98f`. On 2026-10-04 these local components were imported with `git subtree add`
without squashing, retaining their existing commit histories:

| Directory | Previous repository | Imported revision | Import commit |
| --- | --- | --- | --- |
| `plugin/` | `coopnet/dllproto`, `feat/phase2-v2` | `1f270f1` (0.2.0-alpha.5) | `d04a2f7` |
| `relay/` | `coopnet/relay`, `feat/phase2-v2` | `a41a95e` | `3bc6b5c` |
| `npcsync/` | `coopnet/npcsync`, `feat/npc-world-sync` | `6daa126` | `b4dd7b9` |
| `phase1/` | `coopnet/phase1`, `feat/netprobe-audit` | `46d70f0` | `dc4724d` |
| `phase1-check/` | `coopnet/phase1-check`, `master` | `1c37d4a` | `61ea57c` |

The standalone checkouts and published mirror branches are retained as historical
checkpoints. New integrated development belongs here. The subtree import does not
deploy a DLL, alter the game's save files, or enable an experimental feature.

## Contributions adapted from Bukczyk/CP2077-Coop

Reviewed source: [Bukczyk/CP2077-Coop at 20eb125](https://github.com/Bukczyk/CP2077-Coop/tree/20eb125796a4a5e55394cc2c3ed344f9b8456b4a).
The review independently built its portable core on Windows and passed its three
registered suites. Its [same-commit CI](https://github.com/Bukczyk/CP2077-Coop/actions/runs/37234063993)
also passed Windows, Debian and sanitizer jobs. That evidence concerns its core
and frozen import checks, not a new in-game session implementation.

The adopted design contributions are:

- A unified, reproducible project build with portable checks and Windows/Linux CI.
- Explicit session/member identity, world epochs, readiness and entity ownership.
- JOINER intent separated from HOST-approved state, with rejection before mutation.
- Tests for identity/role violations, event ordering, stale epochs, disconnect and
  bounded state rather than relying solely on successful packet decoding.

The authority experiment implements these ideas in the existing Python relay and
uses current CB77 `SCRIPT_MSG` messages. It does not import the incompatible CPS1
wire codec or replace the working native transport/interpolation. Its exact scope
and pending game integration are recorded in `relay/docs/AUTHORITY_EXPERIMENT.md`.

The portable native build compiles this project's existing `plugin/src/v2`
implementation. It is not a renamed copy of the friend's unfinished networking.
The friend's legacy runtime and Caliburn-only vehicle experiment were not installed
over the current game scripts. Research references and their scope are recorded
in the roadmap; no external multiplayer project's game implementation was copied.

## Verified integration checkpoint

Implementation checkpoint **856721b** includes the authority experiment at
**90d5143** and the consolidation/build repairs. Its
[four-job CI run](https://github.com/KyleBuildsAI/cp2077-coop/actions/runs/37259471154)
passed Windows native (15 groups), Linux portable (nine groups), Linux ASan/UBSan
(nine groups), and Windows LuaJIT runtime regressions. The relay suite contains
115 tests, including real UDP authority bootstrap, loss and reconnect. The 21-test
optimized-Python subset also passed locally; it overlaps those 115 tests.

Local full gameplay checks included redscript compilation and returned zero
failed groups with the known A10 sprint assertion waiver. CI explicitly skips
redscript because it has no game assets. Sanitizer builds run the interpolation
benchmark and behavior checks but explicitly skip its uninstrumented timing
budget; Release builds retain that budget. Consolidated phase1 checks passed
31 scoreboard, 33 LuaJIT and 14 real UDP tests; the NPC harness passed 48 tests.

Logs, loopback results and failed-then-repaired CI evidence are retained under
`D:\Downloads\syncfix\bench-artifacts\20261004-integration`. No game was launched,
save modified or new runtime deployed for this integration. Documentation updates
after the verified code checkpoint do not change its gameplay or transport code.
