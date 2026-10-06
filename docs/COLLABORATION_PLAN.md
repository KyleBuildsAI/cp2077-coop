# Kyle and Buck: collaboration proposal

Prepared 2026-10-05. **Proposal for both maintainers to accept.** Buck's quoted offer is not recorded as mutual approval of a migration. See the [verified comparison](BUCK_REVIEW_2026-10-05.md).

## One upstream, one integration path

Recommended upstream for future session/shared-world development: **Bukczyk/CP2077-Coop**. Keep **KyleBuildsAI/cp2077-coop** as the existing playable baseline, research and migration history until the new runtime passes its acceptance gates. Once agreed, new shared gameplay tasks branch from the same pinned upstream; do not develop two competing protocol implementations or automatically synchronize both mains.

Each person works in a separate clone/worktree and task branch. All changes use PRs; agents do not push directly to main, force-push shared branches, merge their own cross-owner work or rewrite someone else's commits. Human maintainers approve interfaces and merges. Existing release authorization still applies after integration: matched source, complete local package, verified release assets and vault updates. Documentation work needs no duplicate runtime release.

Both main branches were unprotected in the read-only check. Proposed administrative follow-up: require PRs, passing platform checks and one other-person review; require code-owner review after a mutually accepted ownership map exists. Do not claim conceptual rules enforce this. No settings changed here.

## Responsibility split

| Area | Proposed lead | Boundary / other reviewer |
|---|---|---|
| Session server, protocol codec, membership, identity, ownership/epochs, catalog/reconnect, interest routing | Buck | Kyle reviews engine assumptions and accepted-result semantics |
| Game-thread adapters, passive humanoid research, actual lifecycle/AI/stimulus/damage hooks | Kyle | Buck reviews identities, authority and interface integration |
| Player appearance/animation, ADS, map/minimap/UI | Kyle | Buck reviews added state/event contracts |
| Vehicle/seat engine integration and observed mount completion | Kyle | Buck owns transport and accepted ownership/seat policy |
| Portable session tests, Debian server packaging and configuration | Buck | Kyle reviews paired build compatibility and live setup |
| Live-game measurements, two-client/two-PC scenarios, complete Windows install package and rollback | Kyle | Buck confirms matched server/protocol versions |
| Shared contracts, integration cutover and release decision | Both | One named author per change; other maintainer reviews |

An area owner is not permission to edit every file in a folder. `CoopPlugin/src/main.cpp` and `runtime/session/cet/CP2077Coop/init.lua` mix responsibilities. Assign one task author for a touched shared file, or split the adapter behind an agreed interface first. Do not let both AI sessions independently rewrite those files.

## Prevent overlap across sleeping hours

The task board and open PRs are the authority for work ownership, not local time or assumptions that the other person is asleep. Use UTC timestamps and full SHAs. No automatic expiry of ownership while someone sleeps.

Before editing: fetch, inspect current upstream/PRs, read the board and latest handoff, claim one task with exact paths and base SHA, then publish that claim through the maintainers' agreed coordination channel. Read/research unrelated areas freely; overlapping edits wait for an explicit handoff. A local unpushed note cannot warn the other person.

At stopping: push the task branch, record last commit, tests actually run, failures, pending processes, exact next action and whether ownership is retained or handed off. The next person fetches and acknowledges the handoff before changing its paths. If the other person's commit appears unexpectedly, stop the overlap, inspect and coordinate; never reset it away.

AI sessions inherit these boundaries. One model per task branch at a time unless the maintainer explicitly assigns parallel non-overlapping subtasks. Reserve expensive models for ambiguous engine work and difficult review; routine builds/docs can use the available model. A stronger model gets no exemption from live evidence, file ownership or review.

## Initial task board

Statuses below are **proposed**, not active claims. Populate branch, base SHA, exact files and acknowledgement when a maintainer accepts a task.

| ID | Proposed owner | Deliverable | Dependency / status |
|---|---|---|---|
| COOP-00 | Both | Accept upstream, ownership, PR/release rules and contract version | Awaiting mutual agreement |
| ENG-01 | Kyle | Capability probe: visible/movable passive humanoid and reversible cleanup | First engine task after COOP-00; research may start independently |
| NET-01 | Buck | Reliable stimulus/hit/result/life baseline proposal and tests | Contract review first; no competing engine implementation |
| ENG-02 | Kyle | Map one confirmed source/target through engine stimulus/hit/reaction hooks | ENG-01 and agreed NET-01 interface |
| INT-01 | Both, one named runner | Matched build, isolated script compilation, rollback, then controlled two-client reaction test | ENG-01/02 + NET-01 and safe deployment gate |
| UI-01 | Kyle | ADS and directional marker follow-up on chosen runtime | Deferred until NPC milestone; preserve current feature requirements |
| VEH-01 | Kyle with Buck policy review | Matching car and actual driver/passenger mount | Deferred; existing vehicle research/backlog retained |

No dates are promised by this board. Keep one implementation task per person active initially. Record blocked dependencies explicitly; do not substitute unrelated features for an acceptance gate.

## Task and handoff template

```text
Task ID / status:
Owner / other reviewer:
Repository / branch / base full SHA:
Exact paths owned; paths explicitly shared:
Contract version / dependency PR:
Acceptance scenario / failure criteria:
Latest pushed full SHA / PR:
UTC stop time:
Checks run and results; checks not run:
Game/save/settings/process state (if a test ran):
Known failures / evidence paths:
Next concrete action:
Ownership retained or transferred to whom; acknowledgement:
```

## Transfer Kyle's work without a bulk merge

Start from [Kyle's research atlas](research/README.md), [video backlog](PLAYTEST_V37_BACKLOG.md) and [release workflow](RELEASE_WORKFLOW.md). Transfer documentation/measurement first, then adapter behavior through contract-specific PRs. Record source revision, authorship and old/new API mapping. Never copy active configuration keys, personal saves or binaries into source.

Do not cherry-pick the complete legacy transport into Buck's typed stack. Do not remove the verified old package until a separately versioned matched installation and rollback are tested. Player/map/vehicle evidence from v0.0.37 must be rerun after porting; compatibility cannot be assumed.

This proposal is published for Kyle's review in his repository. Buck's repository, main, task claims and settings were not changed; no external message was sent. Future upstream PRs require agreement on the intended change and interface.
