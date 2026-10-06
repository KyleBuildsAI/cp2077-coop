# Kyle and Buck: collaboration proposal

Prepared 2026-10-05. **Proposal for both maintainers to accept.** Buck's quoted offer is not recorded as mutual approval of a migration. See the [verified comparison](BUCK_REVIEW_2026-10-05.md).

## Practical rules for every AI session

Kyle clarified on 2026-10-05 that the conversation should build on Buck's named repository and proposed division, rather than repeatedly asking which repository to use. Prepare future shared work for Bukczyk/CP2077-Coop. This clarification does not certify a runtime migration or Buck's acceptance of these additional operating rules.

1. **Read before spending implementation effort.** Fetch upstream and read its instructions, open task/PR records and latest handoff. Check whether the feature already exists. Research can proceed independently; do not duplicate another owner's implementation.
2. **One task, one owner, one branch.** Record a unique task ID, person, agent, base full SHA, exact files and acceptance check in a shared GitHub task record. Create a task branch and link its PR. A private vault entry is not a shared claim. Conflicting claims require maintainer resolution before overlapping edits.
3. **Follow the proposed division.** Buck: server, protocol, identities, ownership, epochs, reconnect and world routing. Kyle: engine hooks, presentation/animation, map/UI, vehicle/seat integration, live measurements and Windows delivery. Shared files need one named editor even when both domains use them.
4. **Agree the calls before building both ends.** Link one versioned interface contract to both tasks. Record exact function names/signatures, producer/consumer, field types, ID meaning, units, thread, authority, lifecycle, return/error values and compatibility. Mark proposed calls as proposed; never invent a second API that merely sounds equivalent.
5. **One implementation of each shared responsibility.** Consumers call the agreed provider. If a provider is unfinished, use an explicitly labeled test stub behind that contract, not another server, codec or production implementation. Contract changes require the other owner to review affected callers before adoption.
6. **Stay inside the claimed files.** If a task needs another owner's path or API changed, record the dependency and request a coordinated change. Continue unrelated owned work. Do not silently broaden the task or refactor shared files.
7. **Define done before coding.** Name the observable result and failure cases. Keep build/unit tests, engine compilation, local game tests and two-PC tests separate. No AI may claim that a passing mock proves a game feature works.
8. **Account for existing work.** Every port records Kyle's source commit, destination paths, what is preserved/replaced/deferred and the integration PR. Reuse existing tests where applicable and rerun behavior after porting. A feature is integrated only after its PR is merged and the stated checks pass.
9. **Leave a usable handoff.** Push the task branch and record UTC time, full SHA, PR, checks/failures, running processes, next action and whether ownership is retained or explicitly transferred. Sleep and an idle AI session do not release ownership.
10. **Review and publish together.** No direct-main writes or unilateral cross-owner merges. After approved integration, publish matched client/server source and packages, verified hashes and release evidence. Update the local complete package and vault for each new runtime/package version.

The AIs do not automatically share local memory. Shared GitHub records and contract files provide that memory; branch rules are procedural unless repository settings enforce them. These rules reduce overlap but cannot guarantee zero conflicts.

### Minimum shared interface record

```text
Contract ID / version / status (proposed or agreed):
Provider owner / consumer owner / task and PR links:
Exact exported calls and types / canonical source path:
IDs, units, clocks, thread and authority:
Creation/update/removal lifecycle and error/overflow behavior:
Compatibility fixture and observable acceptance check:
Changes approved by both owners:
```

For example, Kyle's minimap adapter consumes accepted player identity and pose from Buck's session bridge. Buck owns delivery; Kyle owns marker creation/update/removal. Agree the existing bridge calls to use before writing the adapter. Kyle's marker source is commit 82428358c092afc88147165e39823aed747be26e; its port is not currently claimed or integrated.

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
