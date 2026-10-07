# Physical encounter source mirror

Owner: KyleBuildsAI. UTC checkpoint: 2026-10-07.
Branch: `feat/session-passive-encounter-20261007`.
Starting canonical revision: `bde2cc039a4e455ab678287180afbd52c401d190`.
Upstream implementation: `63b70987c249860097bfc460a3406fcea5bf2837` from
[draft Bukczyk PR #10](https://github.com/Bukczyk/CP2077-Coop/pull/10).
Final upstream evidence: `3aecdc114aee3762f812635bf5ad07793ac3d571`, three Markdown
files only after the implementation revision. This canonical PR is stacked on the existing
[KyleBuildsAI PR #5](https://github.com/KyleBuildsAI/cp2077-coop/pull/5).
Both main branches remain unchanged; this is a draft contribution.

## What is imported

Twenty upstream files are exact, including the experimental REDscript shot/query
probes, local presentation adapter/controller, authored graph/entity sources,
asset builders, lifecycle fixes and tests. Their exact blob IDs and the five
replaced upstream blobs are recorded in `../foundation-import.json`.
The additional upstream `tests/CMakeLists.txt` change is intentionally combined
with the reference-only entry and existing player motor suite. Both source
histories are retained. No protocol, server, native engine boundary or default
entrypoint is redesigned here.

KyleBuildsAI owns the imported engine files and this merge's CMake/provenance/docs.
Bukczyk retains ownership of sessions, identities, authority and shared delivery.
The exact imported file list is the `63b70987c249860097bfc460a3406fcea5bf2837`
refresh entry in the manifest. Local additions are this note and the corresponding
development, roadmap and foundation-integration updates.

## Evidence and limits

The upstream implementation record is
[PHYSICAL_ENCOUNTER_CHECKPOINT.md](../../experiments/shared-encounter/PHYSICAL_ENCOUNTER_CHECKPOINT.md).
Trial 9's actual JOINER firing callbacks produced three geometric query hits on
the exact passive target, including its replacement local ID, plus a sky miss
and an expired-target rejection. Trial 5 demonstrated local reaction/death poses
and corpse retention across two local recreations. These are separate local
engine results; they do not establish synchronized HOST-approved combat.

`SessionBridge::SubmitWorld` still returns `Unsupported`. The geometric capsule
is not native bullet damage or corpse physics, and occlusion, moving targets,
spread and penetration remain open. The controller requires its caller to validate
accepted authority. Its local serial guard is not a distributed event ledger.
Matched HOST trial 11 recorded seven genuine shots, two exact native target-hit
pipelines, observed nonlethal health loss and later persistent death after vanilla
`OnDied`. The initial `OnDeath` callback reported health 0 before the persistent
dead flag became true, so application must await readback. Trial 11's JOINER
selected the idle-only fixture by mistake; its queued poses did not qualify.
Trial 12 corrected only the private template selection and visibly passed held
reaction, idle, death and a fresh-ID recreation preserving the corpse. Its 1,893
bound frames retained exact identity. No JOINER shots occurred in trial 12;
physical query and final presentation evidence come from separate trials.
The [engine answers on PR7](https://github.com/Bukczyk/CP2077-Coop/pull/7#issuecomment-6031592494)
record those observations and the remaining shared-delivery contract.

## Validation

- Windows x64 Release plugin/server/core build: PASS after the merge, using
  `build/canonical-passive`, Visual Studio 17 2022 and the existing pinned SDK.
- Combined 20-test CTest suite: PASS at `2026-10-07T05:09-05:10Z`, including
  the retained player motor and three new encounter suites. Evidence: private
  `20261007-passive-encounter-engine/canonical-20-tests.log`.
- Source parity: PASS, 91 exact upstream files, 176 retained reference files,
  four proposals and 12 contribution files. The combined CMake blob is recorded
  separately. Player presentation and `game-files/latest` remain unchanged from
  the starting canonical revision. Both staged and unstaged `git diff --check` pass.
- Final upstream refresh contains only three Markdown files. No implementation,
  authored asset, build configuration or test changed since the passing combined
  build/test run, so no redundant build was performed.
- [Upstream implementation CI](https://github.com/Bukczyk/CP2077-Coop/actions/runs/37574659468):
  PASS at `63b7098`. Final upstream documentation-head and canonical PR hosted
  checks remain pending at publication.
- Combined mirror live game, multi-PC and larger live groups: NOT RUN.
- No game deployment, public package update or new runtime release by this mirror.

The source merge retains the existing player-presentation work without claiming
that the two experimental paths were live-tested together. The tested public
v0.0.37 / alpha.5 package stays unchanged, with its 70 checksum entries and ZIP
verified. The upstream test owner closed both games/server and restored graphics,
runtime, input and bindings at `2026-10-07T05:23:55.0108458Z`; all 167 current saves
were preserved without rollback. The mirror performed no deployment. Exact
restoration evidence is in the upstream checkpoint and private
`runtime-restore-20261007T0523537922296Z.json`.
