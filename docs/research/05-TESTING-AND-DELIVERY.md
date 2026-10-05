# 5. Testing, performance and safe delivery

[Atlas](README.md) · Supplements the existing [release workflow](../RELEASE_WORKFLOW.md); does not replace it.

## TEST-01: a green command is mistaken for a passing game test

**Their result — documented testing practice and failures.** OPEN//77's [agent-testing guide](https://open2077.net/docs/agent-testing) combines structured state with screenshots, follows the player connection path and turns scenarios into assertions with failure captures. It reports that network-active alone does not prove a live body, teleporting during the continue-screen gate crashed, concurrent clients share saves, and VRAM can limit multiple instances despite reduced graphics. These are vendor observations, not measurements on this machine.

**Our proposal.** Extend our existing harness before installing anything new. Specify process/window identity, live body, role/session, baseline and final restoration checks. Use PID-bound observations, a monotonic timeline, raw pose/seat/camera data and paired screenshots. Preserve the actual human join path; direct debug mutation can bypass the bug being tested.

**Gate.** Every scenario states what was observed versus merely requested, captures both clients on failure and restores test-owned settings without discarding user saves. Headless transport tests and two local windows remain separate from two-PC internet qualification. The external guide's commands and skill are research examples, not authorized installations or instructions for this session.

## TEST-02: rollback restores a prior mod over the user's originals

**Their result — reported fix.** The [September 29 launcher update](https://open2077.net/devblog/2026-09-29-launcher-backups-and-vanilla-restore) describes misclassifying older managed mod files as original user files, then restoring the wrong content. It also reports excluding generated runtime cache from foreign-mod scans and adding bounded log rotation.

**Our proposal.** Keep an installation transaction manifest distinguishing pre-existing user bytes, previous package bytes, newly installed files and generated caches. Test restoration with sentinel user files and interrupted updates. A previous backup is not automatically a pristine baseline. Preserve new play progress separately from configuration restoration.

**Gate.** New versions still refresh source, verified package and release together; docs-only updates do not duplicate the runtime ZIP. Restore only what the transaction owns. Current user saves remain retained; this research makes no installation changes.

## TEST-03: build passes, deployed server fails at runtime

**Their result — open issue.** [Cyberverse #22](https://github.com/TDUniverse/Cyberverse/issues/22) reports GLIBC incompatibility between native-build and managed-runtime container layers caused by insufficient image pinning. The author notes it can appear only at runtime. It remained open at review.

**Our proposal.** For future Linux relay packaging, pin the intended base/runtime and include a startup plus real handshake smoke test in the shipped environment. Compilation alone cannot validate dynamic loading or dependency resolution. Keep diagnostic output and exact image/package identities. No container or runtime was installed in this research pass.

## PERF-01: intermittent hitching looks like network lag

**Their result — reported fixes.** The [September 19 update](https://open2077.net/devblog/2026-09-19-walkable-emotes-and-stability) reports moving resource hot-reload work off the render path and reducing repeated interaction/body queries. It also describes excluding inappropriate vanilla objects from multiplayer loot handling after interior crashes. These are distinct failure classes.

**Our proposal.** Measure per-frame work alongside network timings; cache stable roster/identity data with explicit invalidation. Avoid full crowd enumeration for owned-entity queries. If a visual jump coincides with a frame stall, diagnose that before increasing snapshots. Gate: stable frame cost under the same controlled scene with no missed teardown or entity retirement.

## Proposed experiment record

Record ticket/card, hypothesis, source revision, mod/native/game versions, local/two-PC topology, configuration, save backup reference, process IDs, start/end UTC, monotonic timestamps, scenario steps, observations, numeric results, screenshots/video offsets, failure reasons, restoration result and remaining uncertainty. Separate baseline from candidate. A failed or inconclusive run remains useful evidence; never rename it a pass.

Priority stays: vehicle observer -> isolated failure -> exact car and seats -> shared motion -> ADS/marker -> recovery and measured movement -> two-PC qualification. Fleet stress, automated traffic, new dashboards and platform services wait until the smaller gates pass.
