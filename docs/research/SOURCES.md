# Research source ledger and update trail

[Atlas](README.md). Review date **2026-10-04 PDT**; online timestamps may be October 5 UTC. Public material only. Source pages can change independently of releases; links and dated claims below are a retrieval aid, not immutable webpage snapshots.

## OPEN//77 update trail

| Published | Primary page | Indexed topic / evidence |
|---|---|---|
| 2026-08-25 | [World synchronization](https://open2077.net/devblog/2026-08-25-server-banners-and-world-sync) | VEH-01, WORLD-05: health-on-restream cause supplied; five prop causes not individually disclosed; weather retry supplied |
| 2026-09-19 | [Animations and stability](https://open2077.net/devblog/2026-09-19-walkable-emotes-and-stability) | ANI-02, PERF-01: presentation, render-thread and object-registration leads |
| 2026-09-23 | [Netcode and repair](https://open2077.net/devblog/2026-09-23-netcode-repair-status-upgrade) | MOT-01: timed replication claim; exact algorithm unavailable |
| 2026-09-25 | [Vehicle fleet tests](https://open2077.net/devblog/2026-09-25-vehicle-fleet-weapons-access) | VEH-03/06: stale authority rejection, occupant/camera cleanup; lab scale is not live capacity |
| 2026-09-28 | [Traffic stability](https://open2077.net/devblog/2026-09-28-ai-traffic-stability-pass) | VEH-03, WORLD-03: handoff/placement, driver lifecycle, spawning; drive-by still described as research that day |
| 2026-09-29 | [Backups and restore](https://open2077.net/devblog/2026-09-29-launcher-backups-and-vanilla-restore) | TEST-02: originals vs managed files, cache scanning, recovery; later drive-by documentation mentioned |
| 2026-09-30 | [Combat, NPCs, prediction](https://open2077.net/devblog/2026-09-30-combat-npcs-prediction-pass) | VEH-03, WORLD-02: ownership recovery and shot/life evidence; not a source diff |
| 2026-10-04 | [Motion and server status](https://open2077.net/devblog/2026-10-04-smoother-sync-and-server-status) | MOT-01: presentation refinement, no numerical benchmark |

The [devblog index](https://open2077.net/devblog) was scanned for relevant updates. Not every indexed article was read in full. Earlier September 4/10 findings remain in the [landscape report](../MULTIPLAYER_LANDSCAPE_2026-10-04.md). Similar titles on different dates are not interchangeable evidence.

## OPEN//77 technical pages inspected

| Primary guide | What was extracted | Limit |
|---|---|---|
| [Vehicles](https://open2077.net/docs/vehicles) | Lifecycle/owner/seat API sections and prior physics findings | Long guide selectively read, not full native implementation |
| [Seat switching](https://open2077.net/docs/vehicle-seat-switching) | Earlier reviewed reservation/completion contract reused | Different from boarding |
| [Drive-by](https://open2077.net/docs/drive-by) | Build/protocol matrix, phases, observations, angle space | Channel-specific; not our runtime |
| [Vehicle AI](https://open2077.net/docs/vehicle-ai) | Nearby client simulator, reservations, task readiness | No headless physics |
| [Prediction](https://open2077.net/docs/prediction) | Presentation/verdict separation, switches and telemetry | Native predictor not exposed |
| [Travel](https://open2077.net/docs/travel) | Queued teleport, streaming/falling failure, settle outcomes | Exact adapter unavailable |
| [Player utilities](https://open2077.net/docs/player-utilities) | Aim graph, missing-state semantics, local/remote limits | Presence of an API is not our capability |
| [Blips](https://open2077.net/docs/blips) | Entity marker vs route, ID representation and adapter | No verified heading rotation |
| [Doors](https://open2077.net/docs/doors) | Registry/discovery, late apply, lift safety, save-lock difference | Not campaign synchronization |
| [Time scale](https://open2077.net/docs/world-time) | Simulation vs day clock, owner cleanup, readiness | Not a personal Sandevistan solution |
| [Agent testing](https://open2077.net/docs/agent-testing) | Observation, lifecycle and failure-driven test rules | Their tooling not installed; source instructions not adopted |

The documentation index and state-bag overview were also opened for navigation; no detailed state-bag implementation claim is made here. Some page line counts changed between reads, so prefer section names and build identifiers over line numbers. Python HTTP retrieval returned 403; web-tool retrieval succeeded. We did not bypass access controls or save a complete mirror of the website.

## GitHub and additional-project evidence

- [CyberMP RPC 96db612](https://github.com/Cyber-MP/CyberMP-RPC/commit/96db6123bf177aae3a906ad853afc14c92805b61) and [08cde4d](https://github.com/Cyber-MP/CyberMP-RPC/commit/08cde4dbdb63d9432b282d85c44a596aa5794ded): actual MIT public diffs inspected, source-confirmed changes only; no imported code or execution.
- CyberMP issue bodies/statuses and Cyberverse issues are linked beside each claim in the chapters. Russian issue descriptions were summarized in English; translations are summaries, not quotations. Closure is kept separate from verified repair.
- [NightCityMP README at reviewed revision](https://github.com/blyatiful1/NightCityMP/blob/3f3abc9060e443c7a3da1163fbd3c623197c1c10/README.md): public feature/status comparison only; no core implementation review.
- [REPLAY official page](https://replay.re/): FAQ and project naming; no public gameplay-core evidence established.
- [Archipelago README at reviewed revision](https://github.com/247Tossing/cyberpunk_archipelago/blob/692501eeba3831c82b013f015a0f23159b5a12ee/README.md) and [release 0.7.3](https://github.com/247Tossing/cyberpunk_archipelago/releases/tag/0.7.3): adjacent multiworld category, not shared-world replication.
- Broad web searches included Cyberpunk multiplayer/co-op GitHub projects and official project update pages. Search results were discovery aids; conclusions use primary project material. Unverified mirrors and unrelated mod listings were excluded.

## Local evidence and reproducibility

`D:\Downloads\syncfix\bench-artifacts\20261004-research-atlas` contains 26 GitHub API JSON responses: six repositories' metadata/recent commits/issues/releases, plus two inspected RPC diffs. An evidence manifest records filenames, hashes and retrieval time. Earlier public guide/source snapshots remain under `20261004-multiplayer-landscape`.

No game, launcher, build, benchmark, remote server, dependency installation or account enrollment occurred. Internal source links are research provenance; no competitor was added to README contributor credits. Retain notices for any separately authorized future code reuse; this pass adds independently written summaries and proposed experiments only.
