# Connected encounter source mirror preparation

Owner: KyleBuildsAI. UTC checkpoint: 2026-10-08.
Repository: `KyleBuildsAI/cp2077-coop`.
Local-only branch: `work/canonical-connected-combat-20261008`.
Worktree: `D:/Downloads/syncfix/collaboration/canonical-connected-combat-20261008`.
Starting Kyle PR #6 revision: `2f29c27431c28c3fef38c1ef74d89a5abdbbf2c6`.
Imported candidate: `755ab0cf883ad80b519060814ff45f43f6d25714`, from upstream
preparation branch `work/connected-combat-20261007`, intended for draft PR #10.
Follow-up source: `aa289044907a273a34a6819dafae36a21fc50378`.
Ray diagnostic source: `a8b98c72d426e62e8e2df1ddcad35a402eb8e1bc`.
Current pose source: `97d7c1a0b0fb7751800bf0fa2697769ca4e4b238`.
Final imported documentation: `a5a1e06cbe3975c703a69535f3ff602d2d34620a`.
Local pose merge: `f7e510a2590b4f61f3576e5e31cde67394e44043`.
Initial local merge: `cca5954c591afc94b76dfe92e51cf6527d9268b7`.
Portability local merge: `c0db56a122e38083b5faa4810429ab1241223c38`.
Upstream main prerequisite: `b4a289c350af50ab5695615afd6b12e5121f9409` (PR #11).
Updated pending PR #9: `5526210f669310ae4ba20ad58b8bb8712fd272fc`.

## Scope and dependency order

This isolated source merge preserves both histories and the existing player
presentation research. Its active actuator changes explicitly as described below. Kyle's draft PR #6 remains on its earlier head and stacked on Kyle
PR #5 until the parent task publishes this preparation. Neither main is changed.
PR #9 is pending, not implicitly accepted by including its source here. After it
lands, update upstream PR #10 onto actual main and mirror that tested checkpoint.

The import adds PR #11's existing opaque reliable gameplay route, its generic
game-thread script bridge and a default-off controlled encounter diagnostic.
It does not introduce another wire protocol. Experimental `CPEX1` body meanings
remain a proposal. The HOST validates scoped entity mappings and a ray before
applying its current weapon through the synthetic fixture. The terminal result
reports observed health/life, not an attributed per-request damage amount.
Native bullet collision, remote weapon parity, ammunition consumption, credit,
production combat/death schema and reconnect death restoration are not claimed.

No connected live pass or combined-mirror live test is claimed. Earlier upstream
component results remain separate evidence. This task does not install a runtime,
operate game processes or change saves/settings. The root live-test owner records
those actions and their limitations independently.

## Owned files and conflict resolution

KyleBuildsAI owns this mirror's `tests/CMakeLists.txt` resolution,
`docs/foundation-import.json`, current foundation/development/roadmap/handoff notes,
this validation note, the historical-note pointer and runtime README update.
The manifest lists every exact imported path and its source blob. It preserves
the original import, prior refreshes, reference blobs and player code provenance.
Bukczyk retains ownership of networking/session/server semantics; this merge
imports those bytes unchanged.

The initial connected merge conflicted only in `tests/CMakeLists.txt`; later pose
import `97d7c1a` also conflicted in the runtime README, player entrypoint and
`remote.reds`. The reference-only CMake entry and every upstream suite remain,
including both the inactive motor's regression tests and active pose tests.

The active `init.lua`, `player_pose.lua` and `remote.reds` now exactly match
`97d7c1a`. Only `player_pose` applies movement. The previous `player_motor.lua`,
its tests, presentation experiments, proposed state contract and failed
smooth-tracking evidence stay unchanged. Their previous entrypoint and REDscript
helpers are archived byte for byte in `experiments/player-presentation/reference/`:

- `init_motor.lua`: blob `3d6ddd040fd422fd3f277de584afed2cb1e86fb1`.
- `remote_motor.reds`: blob `cc385beb15481427cae8994cd65e868d07afb296`.

Those files are not loaded, compiled or packaged. Their per-directory attributes
preserve original bytes. The old `playerDiagnostics()` API belongs to the archived
entrypoint; no invented compatibility metrics are exposed by the current one.
The combined lifecycle test exercises the real pose module for two exact actors,
replacement/departure/reconnect/shutdown and explicitly fails if the inactive
motor is also activated. Upstream registry Bind already retires replaced reverse
bindings, so no divergent explicit-unbind implementation is carried forward.
The manifest records prior contribution hashes and this deliberate actuator choice.
Reference sources and public package bytes remain unchanged.

## Validation

Local validation completed by 2026-10-08T03:02Z for the combined `755ab0c` source:

- Windows x64 Release typed plugin/server build: PASS, Visual Studio 17 2022,
  pinned SDK `ad7277714ad30d6885d7050c5ba24fa0102f6920`, parallelism two.
- Combined CTest suite: PASS, 24/24, including preserved player motor, generic
  bridge and 206 connected diagnostic checks. Evidence: `typed-ctest.log`.
- Retained Windows reference native build: PASS with its separate SDK
  `a4a781088a92a8efa890d94fde4efd8985d497c7` and build tree. Reference CTests:
  PASS, 15/15, including clean/bench/lossy/restart real UDP tests on the first run.
- Retained LuaJIT/runtime suite: all other groups passed; the first run reported
  one failed group because this task set TEMP inside the checkout. The sandbox
  tests correctly refused that location. The focused `test_make_sandbox.py`
  rerun with normal TEMP passed S1/S2/S3 and zero failed groups. Both logs remain.
  The existing A10 sprint-lag assertion remains a known failing waiver, not a
  gameplay pass. Legacy REDscript compilation was explicitly skipped in that run.
- Combined typed REDscript and all encounter probes: PASS in isolation at
  `20261008T025529913684Z`; every hashed compiler, game and source input unchanged.
  Evidence: `redscript/compile-20261008T025529913684Z.json`.
- Foundation parity: PASS, 103 exact upstream, 176 retained reference, four
  proposal and 12 contribution files. Source/package preservation and both
  staged/unstaged whitespace checks: PASS.
- Mirror hosted CI, connected live gameplay, combined-mirror live game,
  multi-PC and larger groups: NOT RUN by this subtask.

Evidence lives under this worktree's ignored `build/mirror-evidence/`. Native
trees are `build/connected-mirror` and `build/reference-mirror`. The original
runtime log is `reference-runtime.log`; its focused corrected-setup rerun is
`reference-runtime-sandbox-rerun.log`.

The upstream preparation's Debian GCC 12 Release check exposed a string-formatting
`-Werror=restrict` diagnostic. Upstream correction
`aa289044907a273a34a6819dafae36a21fc50378` is imported through a separate
ancestry-preserving merge. It changes only string assembly in
`shared/src/game_bridge.cpp`, preserving the textual ABI and leaving engine
scripts, player presentation and reference code unchanged. The incremental
Windows Release plugin/server build passed, followed by all three affected
`game_bridge`, `gameplay_bridge` and `connected_encounter` CTests. Evidence:
`typed-build-aa28904.log` and `typed-ctest-aa28904.log`. The original full 24-test
and reference runs remain evidence for the preceding merge; unchanged scripts
and reference sources were not redundantly rebuilt or recompiled.
Final parity recheck at 2026-10-08T03:05Z passed the same 103 upstream, 176
reference, four proposal and 12 contribution files. Final whitespace checks and
unchanged player-code/package comparisons passed. Evidence:
`foundation-parity-aa28904.log`.
Upstream CI for that correction is
[run 37720214660](https://github.com/Bukczyk/CP2077-Coop/actions/runs/37720214660);
the parent task verified all Windows, Debian and sanitizer jobs passed at
`aa289044`. That is upstream evidence, not a mirror hosted pass.

### Controlled ray diagnostics follow-up

Import `a8b98c72d426e62e8e2df1ddcad35a402eb8e1bc` adds 50 lines only to
`experiments/shared-encounter/connected/host_ray.reds`. Its bounded status readback
identifies the rejection stage, exact shooter/target mappings and the first ray
hit. The validation conditions and acceptance return expression are unchanged.
No damage, routing, fixture admission, player motor or reference implementation
is changed. This instrumentation supports a pending live investigation; its
presence does not establish successful connected damage or death presentation.

Affected matched REDscript compilation passed in isolation at
`20261008T031417107646Z`; all hashed game/compiler/source inputs remained unchanged.
Evidence: `redscript/compile-20261008T031417107646Z.json`. Exact source parity
passed for 103 upstream, 176 reference, four proposal and 12 contribution files;
see `foundation-parity-a8b98c.log`. Both whitespace checks passed. No C++ source
changed, so the native
and reference builds above remain applicable. The live-test owner retains the
current trial and final outcome record; this source mirror does not claim that
trial as its own qualification.

### Exact player pose follow-up

Import `97d7c1a0b0fb7751800bf0fa2697769ca4e4b238` adds a bounded per-actor AI
teleport adapter with cancellation, command spacing, actual pose readback and
bounded failure. It addresses the prior frozen-body observation without defining
new networking or combat semantics. The active files have exact upstream blob
parity; the previous motor remains explicitly inactive as documented above.

- Windows x64 Release plugin/server rebuild with parallelism two: PASS.
- Full combined CTest suite: PASS, 25/25. Logs: `typed-build-97d7c1.log` and
  `typed-ctest-97d7c1.log`.
- Real LuaJIT 2.1 lifecycle and pose tests: PASS, including actual Uint64 cdata,
  two independent actor lifecycles and 36 focused pose checks. Evidence:
  `luajit-pose-lifecycle-97d7c1.log`.
- Active typed REDscripts plus connected probes: isolated compile PASS at
  `20261008T034207094726Z`, with every hashed input unchanged. Archived motor
  helpers were correctly excluded. Evidence: corresponding JSON/log under
  `redscript/`.
- Source parity: 108 exact upstream, 176 unchanged reference, four proposal and
  14 contribution files. Both archive blobs equal the previous active blobs.
- Upstream hosted Windows/Debian/sanitizer checks: parent verified PASS at
  `97d7c1a` in [run 37723374154](https://github.com/Bukczyk/CP2077-Coop/actions/runs/37723374154).
  This is upstream CI evidence, not a mirror hosted run.

The separate upstream trial confirmed controlled player placement after roughly
6 m relocation, then rejected a connected shot because the first HOST ray hit
was the authenticated shooter proxy. Native collision disable on that exact
instance did not clear every queried hit shape. HOST health remained 221.400757;
there is no connected damage/death pass. Final source evidence is imported through
the test owner's separate documentation commit, not substituted for mirror live
validation. Smooth movement, remote actions and the combined mirror remain
unqualified in game.

### Final documentation import

Merge `a5a1e06cbe3975c703a69535f3ff602d2d34620a` imports exactly three Markdown
files. It records trial 4's source, controlled placement, returned rejection,
failed scoped collision-disable attempt and outstanding engine gates in the
[upstream live checkpoint](CONNECTED_ENCOUNTER_2026-10-08.md). The two existing
upstream document hashes are refreshed and the new checkpoint is pinned in the
manifest. No runtime source changed, so the preceding build, 25 tests, LuaJIT
and matched script compilation remain applicable.

Final source parity: 109 exact upstream, 176 retained reference, four proposal
and 14 contribution files; see `foundation-parity-a5a1e06.log`. The combined
active init/player_pose/remote still equal `97d7c1a` exactly and both original
motor integration blobs remain preserved. This mirror ends at `a5a1e06`;
subsequent parent-owned collider-free player experiments are not included.

## Handoff and package boundary

The public v0.0.37 / alpha.5 manifest, payload instructions and release assets are
unchanged. No package version is created by this source-only experiment.
Preserve any later root-owned live fixes as explicit follow-up source imports;
do not amend evidence to suggest this earlier tree contained or tested them.
After validation, hand the local commit to the parent task for review and the
existing draft PR #6 update. No push or PR head change is performed by this subtask.
