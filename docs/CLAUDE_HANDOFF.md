# Claude Code handoff — 2026-10-04

## Current instruction

Kyle requested **research, documentation and future plans only** while conserving remaining Codex usage. Claude Code is expected to take over next. Do not interpret earlier game-launch permission or this plan as a request to implement/test now. Preserve the documentation-only scope until Kyle requests development again. No new agent/chat, external message or MCP install was initiated.

## Source of truth

- Repository: `D:\Downloads\syncfix\MP=Jakub`; GitHub `https://github.com/KyleBuildsAI/cp2077-coop`.
- Working branch: `feat/state-sync`; default `main` kept current by safe fast-forward. Fetch/status before editing; preserve Bukczyk's possible concurrent work.
- Pre-research docs checkpoint: **d22a9ee**. This handoff is in a subsequent documentation commit; inspect current HEAD/remote for its exact SHA.
- Vault: `G:\CyberpunkMP`. Read its `AGENTS.md`, `handoff.md`, `RESUME HERE.md` and Release Workflow, plus repo `AGENTS.md` / `CLAUDE.md`.
- Canonical components: `plugin`, `relay`, `npcsync`, `phase1`, `phase1-check`, and main `bin`/`r6`. Old standalone checkouts are historical references.

## Playable version and package

**v0.0.37 + CP2077CoopNet 0.2.0-alpha.5**. Game-tested source `af1f98f`; native historical checkpoint `1f270f1`. Later consolidated source/CI includes `856721b`; headless authority is not a game vehicle bridge.

Local package: `game-files/latest` (copy contents of `game-root`). Release: `v0.0.37-game-bundle.1`.

[Complete installation ZIP](https://github.com/KyleBuildsAI/cp2077-coop/releases/download/v0.0.37-game-bundle.1/CP2077Coop-v0.0.37-alpha5-game-files.zip)

ZIP SHA256: `4205990ab92d0a9754232067e2082ba170df7850acc30bcb97a5153d45032ddc`. Earlier this session a fresh GitHub download matched the local archive and all 69 payload hashes. No new runtime/package was created for this research.

## Machine and save state

Read-only check at **2026-10-04 23:14 PDT** found no Cyberpunk2077 processes. Manual-play helper recorded `settings_restored=true` at **23:04:31 PDT** (2026-10-05 06:04:31 UTC). This is its recorded result, not a fresh independent audit of every configuration file.

Evidence: `D:\Downloads\syncfix\bench-artifacts\20261004-user-play-041710Z\restoration.json`. All 161 original save files were backed up before that session. **User play progress was retained; saves were not rolled back.** Never restore older backups over that progress automatically. Verify process/settings state before any newly requested test. Baseline and Test B are the designated test installs, not the ordinary main install.

`D:\Downloads\Open77Launcher.exe` was checked only for metadata/signature/hash. Its hash matches the published official download, it is unsigned, and it was not run. Do not launch it under this scope.

## Proven and open

- Local two-game v2 connection, remote stand-in and map/nearby minimap marker work; controlled single test-NPC pose/lifecycle evidence exists. RTT is not visual latency.
- Player movement still lags/corrects. A10 long-sprint assertion remains waived, not fixed. Retained steering stays off without a matched improvement.
- Cars remain cosmetic replicas. Model/appearance, seats, physics ownership and damage agreement remain open. Kyle's video shows severe airborne-car/camera disruption at 01:33–01:45.
- User requirements: visible remote ADS, teammate icon with facing arrow, matched cars and two-person shared rides.
- Native v2 authoritative combat, broad NPC AI, inventory/world/quests/persistence and real two-PC qualification remain unfinished.
- Baseline flags were `npc_test=false`, `native_retarget=false`, bots/trace off and standalone probes disabled. Verify actual configs rather than assuming. Preserve one native poll owner.

## Read next

1. [Landscape research](MULTIPLAYER_LANDSCAPE_2026-10-04.md): OPEN//77, CyberMP, Cyberverse, Choomlink, source boundaries and independent implementation ideas.
2. [Video backlog](PLAYTEST_V37_BACKLOG.md): eight open tickets, timestamps and acceptance checks.
3. [Canonical plan](MULTIPLAYER_PLAN.md) and [engine/API research](RESEARCH_NOTES.md).
4. [Bukczyk comparison](BUKCZYK_CHECKPOINT_COMPARISON.md): checkpoint 20eb125, session policy and work-in-progress, not a completed new runtime.
5. Vault notes `Vehicle Authority Readiness` and `Remote Player Map Marker` for earlier failed approaches.

## Work sequence once Kyle requests implementation

1. Create a small API knowledge pack from **our** source and installed versions: signatures, scope/thread, units, async completion, evidence and known failures. Markdown/JSON first; an Open77-specific MCP cannot provide our missing native APIs.
2. Specify/add a scoped vehicle observer, compile, then run a newly requested backed-up stationary probe. Log stable IDs/model/appearance, actual parent/seat, physics owner/epoch, transform/velocities, corrections and pause state.
3. Diagnose the video failure before changing physics flags. Public vendor reports about kinematic proxies/mesh bodies are leads, not verified mechanisms. Separate world authority from any future driver simulation lease.
4. Prove matching parked car and confirmed driver/passenger seats before motion. Then qualify moving rides and cleanup.
5. Probe ADS state/remote pose and directional map rendering separately. Animation is not damage authority.
6. Refine readiness/pause/load recovery and measured movement. Extra players, arbitrary traffic and campaign expansion remain later gates.

## User preferences and release rules

- Every **new version**: push source, keep default README current, refresh complete local package, publish matching release assets, verify hashes and update vault. Preserve previous releases. Documentation-only edits are pushed without repackaging unchanged runtime.
- Use full versions such as **v0.0.37**. README below Run stays concise: one bullet per version, detailed docs linked separately.
- Preserve prominent ZIP link, screenshot/caption and credits: **KyleBuildsAI**, **Bukczyk**, AI assistance **Claude Code**, **ChatGPT**, **local AI models**.
- Kyle says he invited Bukczyk as collaborator; access/acceptance was not checked or modified. Coordinate ownership and use reviewable branches/PRs. Do not infer permission to message him from this research request.
- Keep provenance for research; do not relabel third-party code as original or remove required notices if later reused. No competitors added as contributors. Tilted Phoques implementation remains excluded by the prior research decision.

## Validation and evidence

Primary online sources and selected pinned interfaces reviewed; launcher hash/metadata and session restoration record read. Documentation diffs/links checked before publication. No new game test, benchmark, build, dependency change or deployment. Local research evidence: `D:\Downloads\syncfix\bench-artifacts\20261004-multiplayer-landscape`.
