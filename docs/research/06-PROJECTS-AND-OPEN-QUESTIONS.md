# 6. Projects, maturity and unresolved questions

[Atlas](README.md) · Extends the [original landscape](../MULTIPLAYER_LANDSCAPE_2026-10-04.md). Checked 2026-10-04 PDT. A project's README is its own report, not our gameplay validation.

## Newly examined projects

| Project | Primary evidence | What we can usefully learn / limits |
|---|---|---|
| [NightCityMP](https://github.com/blyatiful1/NightCityMP) | README calls it a continuation of Tilted Phoques CyberpunkMP targeting 2.31a, reports Linux/Windows play over Tailscale, appearance/equipment/movement/time/chat, and explicitly excludes NPC/traffic/world/quest/PvP-damage sync. | Relevant host/join UX comparison. Its roadmap and install instructions mix planned menu flow with current hotkey flow; do not assume one-click hosting is complete. README says no public release and API returned none. Custom license/lineage: implementation not reviewed or imported. |
| [REPLAY / BlackICE](https://replay.re/) | Official FAQ advertises Cyberpunk dedicated-server multiplayer and future source publication; no release date. Its privacy page names BlackICE. | A separate project to track, not a discovered public engine implementation. No source repository, native mechanism or demonstrated fix established from the inspected official page. Privacy/infrastructure descriptions do not prove gameplay features. |
| [Cyberpunk Archipelago](https://github.com/247Tossing/cyberpunk_archipelago) | README describes progression checks, rewards, restrictions and optional shared deaths; API lists experimental release 0.7.3 on September 25. | Adjacent multiworld progression integration, not same-world avatar/vehicle co-op. Future research could study event completion and reward delivery concepts. No code imported; no license identified in repository metadata, so reuse is not cleared. |

NightCityMP is a continuation, not independent evidence that an additional native architecture solved the same problem. Archipelago is a different multiplayer category. Do not inflate the number of independently working shared-world solutions by counting forks and adjacent projects alike.

## Rechecked repositories and pinned revisions

| Repository | HEAD at review | Latest push UTC / observations |
|---|---|---|
| blyatiful1/NightCityMP | `3f3abc9060e443c7a3da1163fbd3c623197c1c10` | 2026-07-20; no GitHub releases; license NOASSERTION |
| 247Tossing/cyberpunk_archipelago | `692501eeba3831c82b013f015a0f23159b5a12ee` | 2026-09-25; release 0.7.3; license metadata null |
| Cyber-MP/CyberMP-Types | `e8625b0ea66d55fcb702e55402714d2693c9bbd5` | 2026-09-04; same as earlier review; MIT |
| Cyber-MP/CyberMP-RPC | `3ed25183a0ed72fdbdd1fc116192bfc35b69e94b` | 2026-09-04; commit labels v0.2.1, while newest returned GitHub Release is v0.1.0-rc.6; release objects and source/package versions differ |
| Cyber-MP/CyberMP-Freeroam | `42e5fa0441c3ed3962e9eba3b70eab4e97255298` | 2026-09-10; no GitHub releases returned; MIT |

API metadata, recent commits, releases and up to 100 recent issues per repository were saved locally for these and Cyberverse. PR entries were excluded when interpreting issue states. This is a bounded snapshot, not an exhaustive history or package-registry audit.

## What remains unresolved elsewhere

| Source | Observed status | Meaning for our planning |
|---|---|---|
| [CyberMP Freeroam #100](https://github.com/Cyber-MP/CyberMP-Freeroam/issues/100), [#98](https://github.com/Cyber-MP/CyberMP-Freeroam/issues/98), [#82](https://github.com/Cyber-MP/CyberMP-Freeroam/issues/82) | Open: focus, spectating, large-player-count optimization | Do not claim these are solved because the platform has a beta. |
| [CyberMP Types #17](https://github.com/Cyber-MP/CyberMP-Types/issues/17), [#11](https://github.com/Cyber-MP/CyberMP-Types/issues/11) | Open: redscript-to-types generation and native documentation | API knowledge tools themselves can be incomplete. Build our cards from our exact source/runtime. |
| [Cyberverse #4](https://github.com/TDUniverse/Cyberverse/issues/4), [#5](https://github.com/TDUniverse/Cyberverse/issues/5), [#6](https://github.com/TDUniverse/Cyberverse/issues/6) | Open: weapons, damage/health, locomotion | Useful problem decomposition, not completed multiplayer subsystems. |
| [Cyberverse #22](https://github.com/TDUniverse/Cyberverse/issues/22) | Open: runtime container incompatibility | Require execution evidence in addition to compilation. |
| [OPEN//77 drive-by guide](https://open2077.net/docs/drive-by) | Specific unstable builds/protocols; stable exclusion in page | Current guides and broad marketing can describe different release channels. |

**Already known projects:** OPEN//77 and CyberMP have the strongest inspected public platform documentation/interfaces; Cyberverse remains a developer framework; Choomlink remains a planning/fork effort in the earlier inspected evidence; Bukczyk's checkpoint comparison remains the relevant collaboration record. Tilted Phoques core implementation remains excluded under the existing research boundary. New searches also returned mirrors/forks and generic mod-download pages; these were not promoted into independently verified projects.

## Questions requiring evidence we do not have

1. OPEN//77's precise interpolation/clock algorithm, collision rollback implementation and native ADS adapter.
2. A verified rotating teammate-arrow implementation compatible with our installed engine/UI path.
3. CyberMP core replication internals, measured supported load and an independently reproduced solution to our car instability.
4. Current-build compatibility and end-to-end test logs for Cyberverse/Choomlink.
5. Public BlackICE implementation or test evidence beyond official project statements.
6. Full shared campaign/quest/save semantics for any candidate that match our host-save objective.

The correct answer is **unknown** until evidence closes each question. Faster progress comes from borrowing a testable design idea and proving it in our bridge, not assuming a competitor's feature list supplies missing engine code.
