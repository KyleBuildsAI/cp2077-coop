# Working on one shared project

2026-10-05 collaboration proposal: read [the ownership and handoff rules](COLLABORATION_PLAN.md), [Buck's current source review](BUCK_REVIEW_2026-10-05.md) and [the shared NPC acceptance contract](SHARED_NPC_MILESTONE.md). Future upstream migration is proposed, not completed. Task branches and PR review supersede the earlier direct-main documentation publication practice for new collaboration work.

Use this repository as the canonical codebase. Each person or AI works in a local
clone and a feature branch/worktree, then submits a small pull request. Review both
the code and its evidence before merging. A common repository does not mean two
agents should edit the same working file concurrently.

| Area | Source | Primary responsibility |
| --- | --- | --- |
| Game integration | `bin/`, `r6/` | Players, markers, owned actors and observed engine lifecycle |
| Native networking | `plugin/` | RED4ext API, transport, clock and interpolation |
| Relay and authority | `relay/` | Membership, validated routing and experimental entity policy |
| Controlled NPC harness | `npcsync/` | Isolated actor protocol/lifecycle experiments |
| Measurement | `phase1/`, `phase1-check/`, `coop-tools/` | Capture, analysis and reproducible evidence |
| Shared checks | `tests/`, root CMake and `.github/workflows/` | Regression and portability gates |

Agree on one message contract and acceptance test before changing both ends of a
protocol. Host identity, entity identity, local engine EntityID and session epoch
are different things. Keep their meanings explicit in APIs and logs.

## Checks

Use the root build scripts for portable native checks. CMake also supports a direct
configure/build/test workflow without a Cyberpunk installation:

```powershell
cmake -S . -B build/core
cmake --build build/core --config Release --parallel
ctest --test-dir build/core -C Release --output-on-failure
```

Use `scripts/build.ps1 -NativePlugin` on Windows to include the pinned SDK build,
DLL loading and actual UDP loopbacks. `scripts/build.sh` runs the portable suite on
Linux; `COOP_SANITIZE=ON COOP_BUILD_TYPE=Debug bash scripts/build.sh` also instruments
the C++ units with ASan/UBSan. Python 3.12 or newer is required for consistent
cross-language golden results.

The opt-in ownership experiment and its commands are documented in
[`relay/docs/AUTHORITY_EXPERIMENT.md`](../relay/docs/AUTHORITY_EXPERIMENT.md).
It is disabled by default and has no game adapter yet.

The existing game-script regression runner remains available:

```powershell
# Full local checks, including redscript sandbox compilation; no game is launched.
powershell -NoProfile -ExecutionPolicy Bypass -File tests/run_all.ps1 -KeepWorkDir

# CI subset, explicitly excluding compilation against installed game assets.
powershell -NoProfile -ExecutionPolicy Bypass -File tests/run_all.ps1 -SkipRedscript -KeepWorkDir
```

Set `COOP_GAME_DIR` for a different local read-only compiler reference. The known
A10 measured sprint-lag failure remains a documented waiver; zero failed groups
does not mean every assertion passed. Game-free CI cannot verify animation,
collision, mounting, map visibility or shared-world behavior.

The existing native plugin's full Windows build remains in `plugin/`; its sibling
relay path now resolves to this repository's `relay/`. `plugin/tools/fetch_deps.ps1`
pins RED4ext.SDK to `a4a781088a92a8efa890d94fde4efd8985d497c7`. Do not substitute a
different SDK just because another project uses it. Existing component READMEs
may contain historical standalone commands; prefer these canonical paths.

The historical protocol-1 measurement harness is `phase1/run_tests.ps1`. It exports
the original plugin v0.1.2 commit from retained Git history instead of accidentally
compiling current v2 with the old shim. Use a full clone for that historical check;
a shallow clone must first fetch the referenced history. Current v2 integration
is covered by the root native transport loopbacks and relay tests.

## Collaboration and releases

**Standing instruction:** every new version updates both GitHub and the local
complete installation package, then the Obsidian handoff/status. Follow the
[required release workflow](RELEASE_WORKFLOW.md); source-only pushes are not a
completed version. This recurring work is already authorized by Kyle.

The current complete install batch has its own entry point at
[`game-files/latest/`](../game-files/latest/README.md). Its generated `game-root/`
contains the files copied into Cyberpunk, and its GitHub release ZIP includes
the pinned runtimes, matching relay, configuration examples and SHA256 manifest.
`scripts/package_game_files.py` verifies every input before generating that batch;
DLLs and third-party payloads remain release assets, outside source control.

Suggested ownership split: one person owns game integration/live testing, another
owns session policy/builds, and each reviews the other's cross-component changes.
Record active tasks and exact file ownership in the pull request or task discussion.
Use `AGENTS.md` for shared rules and `docs/MULTIPLAYER_PLAN.md` for acceptance gates.

Keep `main` or the chosen integration branch protected with required checks and
human review once repository administrators configure it. This document does not
claim permissions or branch-protection settings were changed automatically.

Release only matching Lua, redscript, plugin and relay revisions. Preserve original
component authorship/history and document migrations. Source imports and CI changes
do not change the deployed gameplay version: the last live-tested runtime remains
v0.0.37 with plugin alpha.5 until a later game change is compiled and tested.

Obsidian is the local lab notebook and handoff; keep its current status linked to
the repository roadmap. Reports must distinguish simulated delay on one PC from
a real internet test between two players.
