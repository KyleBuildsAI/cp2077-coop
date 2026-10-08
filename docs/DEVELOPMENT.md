# Working on the shared multiplayer foundation

The product goal is large multiplayer co-op with dynamic groups. Use the imported
Bukczyk foundation for new work. `Bukczyk/CP2077-Coop` remains the collaboration
upstream; this KyleBuildsAI repository is the aligned working copy for game-side
development and preserved prototype features. See the exact revision, changed
files and evidence in [foundation integration](FOUNDATION_INTEGRATION.md).

Source integration does not port every feature or qualify a new game package.
The existing **v0.0.37 / alpha.5** download remains the tested two-client prototype.
Do not mix its DLLs or scripts with the new typed session bridge.

The current source preparation is tracked in
[CONNECTED_ENCOUNTER_MIRROR.md](validation/CONNECTED_ENCOUNTER_MIRROR.md).
It imports PR #11's reliable generic gameplay route and a default-off engine
diagnostic prepared for draft PR #10. PR #9 remains pending. Use the existing
`GameplayIntent` / `GameplayResult` contract; the experimental `CPEX1` body is a
proposal, not production combat. The older `SubmitWorld` method remains unsupported
and is not the new route. No connected or combined-mirror live pass is claimed.

## File ownership

| Area | Paths | Lead responsibility |
| --- | --- | --- |
| Typed protocol, sessions and networking | `shared/`, `SessionServer/`, `docs/PROTOCOL.md` | Bukczyk |
| Native engine boundary | `CoopPlugin/` | Coordinate changes: engine hooks with KyleBuildsAI, session interface with Bukczyk |
| In-game presentation and lifecycle | `runtime/session/` | KyleBuildsAI, coordinated with Bukczyk's current NPC work |
| Measurement and live evidence | `coop-tools/`, test traces and recorded runs | KyleBuildsAI |
| Shared regression and release gates | `tests/`, `build-support/`, root CMake, CI and packaging | Owner declared for each task |
| Retained prototype | `bin/`, `r6/`, `plugin/`, `relay/`, `npcsync/`, `phase1/`, `phase1-check/` | Reference sources and tests to adapt, not a parallel protocol |

Use feature branches and small PRs. Before editing, inspect current upstream work
and claim exact files in the task. Preserve unrelated changes. Use existing calls
where they fit; do not wait for another maintainer just to locate documented code.
Agree any changed shared data contract and its acceptance test before altering
both ends. Publish a UTC handoff with revision, files, tests and next action.
The collaboration/reuse PRs are proposals until Bukczyk accepts them.

## Build the typed foundation

Requirements: CMake 3.21+, C++20, Git and a supported compiler. Windows needs the
Visual Studio x64 C++ tools and Windows SDK. Linux needs GCC 12+ or an equivalent
C++20 compiler. Commands below build/test source; none installs or launches a game.

```powershell
# Windows: typed plugin, session core/server, tests and the frozen legacy relay.
./scripts/build.ps1

# Portable core/server/tests without RED4ext SDK or the Windows legacy relay.
./scripts/build.ps1 -CoreOnly

# Optional verified local copy of the pinned typed-plugin SDK.
./scripts/build.ps1 -SdkSource D:/path/to/RED4ext.SDK
```

```sh
# Linux: typed core, session server and automated checks.
bash scripts/build.sh
```

Equivalent portable configuration, using a fresh build directory:

```powershell
cmake -S . -B build/session-core -DCOOP_BUILD_PLUGIN=OFF -DCOOP_BUILD_LEGACY_SERVER=OFF
cmake --build build/session-core --config Release --parallel
ctest --test-dir build/session-core -C Release --output-on-failure
```

The typed plugin pins RED4ext.SDK to
`ad7277714ad30d6885d7050c5ba24fa0102f6920`. The current bridge and known game
limitations are in [runtime/session/README.md](../runtime/session/README.md).
The generated session server is `SessionServer/CP2077SessionServer` under the
chosen build directory, with the configuration subdirectory and `.exe` on
multi-configuration Windows builds. `scripts/package-session.ps1` prepares matched
development profiles; this does not make them a qualified public game package.

## Check the retained prototype separately

The old CB77 code retains its own tests and SDK pin. It is valuable porting evidence,
but must not share a CMake build directory with the typed plugin.

```powershell
./scripts/build-reference.ps1
./scripts/build-reference.ps1 -NativePlugin
```

```sh
bash scripts/build-reference.sh
```

`COOP_REFERENCE_ONLY=ON` selects only the retained reference CMake checks.
The reference plugin pins RED4ext.SDK to
`a4a781088a92a8efa890d94fde4efd8985d497c7`; do not substitute either stack's SDK
for the other. Use Python 3.12 or newer for reference Python checks. The original
protocol-1 measurement harness remains in `phase1/run_tests.ps1` and needs the
retained full Git history.

```powershell
# Legacy script checks, including compilation against a read-only game reference.
powershell -NoProfile -ExecutionPolicy Bypass -File tests/run_all.ps1 -KeepWorkDir

# Explicitly omits that installed-game redscript compile.
powershell -NoProfile -ExecutionPolicy Bypass -File tests/run_all.ps1 -SkipRedscript -KeepWorkDir
```

Set `COOP_GAME_DIR` for another read-only game reference. These checks apply to
the retained runtime, not automatic qualification of `runtime/session/`. Its known
A10 sprint-lag assertion is a tracked waiver, not a pass.

## Evidence and releases

The current source integration results belong in
[FOUNDATION_INTEGRATION.md](FOUNDATION_INTEGRATION.md), not inferred from commands
listed here. Distinguish mocked APIs, headless sockets, compiled native/script
bridges, two-game smoke tests and real multi-PC group runs. Sixteen headless
members do not establish sixteen live players. Qualify group sizes gradually and
publish only the supported capacity demonstrated by the release matrix.

Follow [RELEASE_WORKFLOW.md](RELEASE_WORKFLOW.md) for every requested new runtime
or package version. Source, matched install files, release assets, verified hashes
and vault notes must agree. Preserve [the existing package](../game-files/latest/README.md)
until its replacement passes installation, rollback and game gates. Source-only
alignment does not justify replacing a tested package with unqualified binaries.

The first joint engine goal remains one stable NPC with safe projection, then a
JOINER stimulus/hit whose result is decided by HOST and rendered by all clients.
The [current roadmap](MULTIPLAYER_PLAN.md) separates that work from feature ports
and broader group qualification. Do not infer working AI suppression, seats,
combat or campaign support from passing transport tests.
