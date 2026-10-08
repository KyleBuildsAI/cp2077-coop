# Claude Code handoff - large co-op foundation

## Current connected-source preparation - 2026-10-08 UTC

Use `work/canonical-connected-combat-20261008` in
`D:/Downloads/syncfix/collaboration/canonical-connected-combat-20261008`.
It merges candidate `755ab0cf883ad80b519060814ff45f43f6d25714` from the upstream
preparation branch into Kyle PR #6's `2f29c27`, preserving upstream ancestry,
player presentation research and reference sources. The candidate contains PR #11's
generic routing plus the script bridge and default-off encounter diagnostic.
The subsequent import `aa289044907a273a34a6819dafae36a21fc50378` corrects GCC 12
string-formatting compilation without changing the textual bridge ABI.
Import `a8b98c72d426e62e8e2df1ddcad35a402eb8e1bc` adds bounded HOST ray
readback. Pose import `97d7c1a0b0fb7751800bf0fa2697769ca4e4b238` replaces
ineffective NPC placement with one owned, bounded AI teleport command and actual
readback. Active init/player_pose/remote match upstream exactly. The prior motor,
its tests and presentation research stay inactive; its previous entrypoint and
REDscript helpers are archived byte for byte outside packaged runtime. Do not
activate both controllers or expect the archived playerDiagnostics API.
Current Windows Release build, all 26 CTests and separate LuaJIT
pose/lifecycle/passive tests pass. The preceding isolated matched REDscript
compilation applies to the unchanged scripts. Live evidence belongs to the
upstream test owner and does not qualify this mirror or smooth movement.
Documentation checkpoint `a5a1e06cbe3975c703a69535f3ff602d2d34620a` records
controlled placement passing, but the
HOST ray still hit the authenticated shooter proxy. There was no connected damage
or death. Current source import `481609a57ccf775657818fe059f6b87652836ef3` adds the
false-by-default passive static-player option from `8416637`. It replaces Judy
only when explicitly enabled and retains exact bindings/observed cleanup. Active
runtime bytes match that upstream head. Automated tests do not prove its live
collision behavior or successful connected damage. Trial 5 results and final restoration are now imported through docs-only
`0a8103e316d1994d6be05123fd81d599b8de5bb1`: two connected requests yielded
observed HOST health/death and the same JOINER body's reaction/death pose. F9
also triggered quickload; subsequent reconnect restored an idle projection,
so corpse recreation and terminal-state recovery remain unqualified.
PR #9 at `5526210f669310ae4ba20ad58b8bb8712fd272fc` is still pending; draft PR #10
must remain separate. Kyle PR #6 is published at `b361c8e`, still draft, with typed and reference CI
passing on first attempts. The current follow-up imports documentation only.

Read [the exact merge and validation record](validation/CONNECTED_ENCOUNTER_MIRROR.md)
before continuing. KyleBuildsAI owns this merge's CMake/provenance/documentation
and the engine diagnostic. Bukczyk owns session/server/network contracts.
The `CPEX1` body is a proposal and HOST-current-weapon fixture, not a production
remote hit/damage model. The upstream controlled pass does not establish a
combined-mirror live test. Public v0.0.37 / alpha.5 package bytes are unchanged. This subtask does not
operate games; the root live-test owner records installation and process state.

Next: retain runtime `481609a` and the verified public package. Publish this
validated documentation follow-up through existing draft PR #6 without pushing
main, then record its hosted checks. Prioritize non-conflicting recreation input
and agreed accepted-state restoration before wider combat claims. After Bukczyk merges PR #9, update PR #10 against that
actual main and mirror its tested revision. The older sections are historical.

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

## Previous lifecycle handoff - 2026-10-06 UTC

KyleBuildsAI implemented the requested CET startup and repeated-reconnect fixes.
[Bukczyk PR #4](https://github.com/Bukczyk/CP2077-Coop/pull/4) contains the tested
change for Bukczyk review. Upstream head:
`9b0d7065dc4e853ddc32b6b221330e412b403561`; tested runtime code: `89d619e`.
The canonical local checkout is on `fix/cet-session-lifecycle-local` with the same
runtime and test files, explicit import-manifest provenance and local handoff
updates. Neither main has been changed by this task.

Read [the evidence and exact owned files](validation/CET_SESSION_LIFECYCLE.md).
KyleBuildsAI retains ownership of PR corrections. Existing native/network calls
are unchanged. All 12 local CTests, LuaJIT regressions and code-commit hosted
Windows/Debian/sanitizer checks passed. Fresh HOST/JOINER testing showed stable
membership, successful actual reconnect and proxy removal after JOINER exit.
This does not fix or certify movement, shared NPC authority or larger live groups.

Both test games and the private server are closed. Graphics and hotkey bindings
were restored; the separate diagnostic mod was archived outside the game. Saves
were unchanged. The authorized Baseline/Test B copies retain the exact corrected
Lua and matched typed native stack. Evidence/backups are in
`D:/Downloads/syncfix/bench-artifacts/20261006-cet-lifecycle`; private keys stay local.

No new versioned runtime/package release was requested or published by this
source PR. Preserve `game-files/latest` v0.0.37 / alpha.5. The source correction
must not silently replace that complete package while movement remains open.
Next: Bukczyk reviews this focused PR; a separate, explicitly assigned task can
port and measure the earlier movement safeguards against the typed bridge.

## Previous foundation checkpoint

Updated 2026-10-05. Source merge completed at **2026-10-06T04:41:09Z**.

## Current direction

KyleBuildsAI requested and authorized integrating Bukczyk's foundation into this
repository. That source work is merged. The goal is **large multiplayer co-op
with dynamic player groups**, not a two-player product limit.

[Bukczyk/CP2077-Coop](https://github.com/Bukczyk/CP2077-Coop) remains the
collaboration upstream. New game-side work uses `runtime/session/` and the typed
`shared/`, `CoopPlugin/`, `SessionServer/` foundation. The old `bin/`, `r6/`,
`plugin/` and `relay/` trees preserve features and tests for deliberate ports.
They are not a competing network design. Feature ports are still pending.

Read `AGENTS.md`, [foundation integration](FOUNDATION_INTEGRATION.md),
[development instructions](DEVELOPMENT.md) and the current section of
[the roadmap](MULTIPLAYER_PLAN.md). Also read `G:\CyberpunkMP\handoff.md` and
`RESUME HERE.md`. Fetch/status before work; use a new task branch and PR, exact
file ownership and UTC handoffs. Do not resume the old `feat/state-sync` workflow
or push directly to main.

## Merged source and verification

- [KyleBuildsAI source PR #3](https://github.com/KyleBuildsAI/cp2077-coop/pull/3)
  merged as `9d019e8f00abe712f0786ca8f30420be1a9f6130`.
- Integration head: `ba67405a80d2b209a331b95f17cbd80d3e3ec964`.
- Imported Bukczyk revision: `7e3826d1c313595a4784f1b232e10cec222b6ca3`.
  Both histories are retained; the import manifest checks exact source parity.
- Local Windows checks: typed native/server build and **11/11 CTests** passed;
  retained reference native build and **15/15 CTests** passed.
- Imported REDscript compiled with Codeware in an isolated sandbox.
- All **seven distinct hosted checks** passed across
  [typed CI](https://github.com/KyleBuildsAI/cp2077-coop/actions/runs/37414562588)
  and [reference PR CI](https://github.com/KyleBuildsAI/cp2077-coop/actions/runs/37414562568).
- No installation, game launch or live group test occurred in this integration.
  A sixteen-member headless test does not prove sixteen live players.

Detailed evidence: `D:\Downloads\syncfix\bench-artifacts\20261005-foundation-integration`.
Use separate typed and reference build directories because their SDK pins differ.

## Package boundary

The public and local tested package is still **v0.0.37 / alpha.5** at
`game-files/latest/`. Its ZIP and all 69 payloads remain unchanged.
[Download the existing prototype](https://github.com/KyleBuildsAI/cp2077-coop/releases/download/v0.0.37-game-bundle.1/CP2077Coop-v0.0.37-alpha5-game-files.zip).

Private matched development profiles were generated under
`artifacts/session-20261005-213421-701/`. They contain a private test key; keep them
unpublished. They are not a qualified replacement package. Do not overlay typed
DLLs/scripts onto the old prototype or infer gameplay readiness from compilation.

Every requested new runtime/package version follows
[RELEASE_WORKFLOW.md](RELEASE_WORKFLOW.md): verified matching source, complete
local package, release assets/hashes and vault notes. Source-only integration
does not require publishing unchanged runtime bytes again.

## Useful next task

Use the typed bridge for the next explicitly requested game-side task. The first joint goal is
one stable, safely controlled NPC, followed by HOST-owned reactions to JOINER
stimuli/hits. Check upstream ownership before editing
`runtime/session/cet/CP2077Coop/npc_population.lua`, `npc_runtime.lua` or their
tests. Existing calls are available locally; no reply is needed just to inspect
them or prepare an isolated engine experiment.

Adapt measured spawn placement and confirmed-deletion safeguards without replacing
Bukczyk's exact IDs/catalog/generations. Passive JOINER AI remains unresolved.
Add lifecycle regressions and matched script/native checks before live acceptance.
A queued request, cleared tag or successful DeleteEntity call is not completion.
Keep broad ambient-population suppression disabled.

Bukczyk owns protocol/session/server authority; KyleBuildsAI owns engine
integration, presentation, maps, vehicle/seat hooks and live measurement.
Coordinate shared-interface changes. The collaboration and reuse proposals in
Bukczyk PRs #1, #2 and #3 are not approved merely because this source merge landed.
Do not start unrelated implementations or send collaborator messages without the
user's request.

## Historical evidence and save protection

The [2026-10-04 handoff](history/CLAUDE_HANDOFF_2026-10-04.md) is preserved
byte for byte. Its research-only scope, working branch and old source directions
are superseded. Its relative links were written for the original `docs/`
location; use the current indexes below to reach those documents.

Its process/settings checks describe October 4, not the present machine state.
User play progress was retained then; never restore old save backups over newer
progress automatically. Before an authorized live test, verify processes and
settings, back up current saves/configuration, use designated test installations
and restore test changes. The older launcher inspection did not execute it.

Useful retained evidence: [research atlas](research/README.md),
[source ledger](research/SOURCES.md), [landscape review](MULTIPLAYER_LANDSCAPE_2026-10-04.md),
[video backlog](PLAYTEST_V37_BACKLOG.md), [engine research](RESEARCH_NOTES.md)
and [older comparison](BUKCZYK_CHECKPOINT_COMPARISON.md).
The legacy A10 sprint-lag waiver, cosmetic vehicles, missing shared seats and
unfinished visible ADS/directional markers are still open; source alignment did
not fix them.
