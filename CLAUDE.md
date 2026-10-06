# Shared project instructions

Read `AGENTS.md`, `docs/FOUNDATION_INTEGRATION.md`, `docs/DEVELOPMENT.md` and
`docs/RELEASE_WORKFLOW.md`. The Obsidian handoff is `G:\CyberpunkMP\handoff.md`.

The goal is large multiplayer co-op with dynamic groups. Work against Bukczyk's
imported typed foundation in `shared/`, `CoopPlugin/`, `SessionServer/` and
`runtime/session/`. Keep protocol contracts aligned with `Bukczyk/CP2077-Coop`.
The old `bin/`, `r6/`, `plugin/` and `relay/` trees preserve working features and
tests for deliberate ports; they are not a second network roadmap.

Use task branches and PRs, exact file ownership and UTC handoffs. Implement only
the requested scope. A source merge is not a playable release or Bukczyk's
approval of outstanding proposals.

Every requested new runtime/package version must update GitHub and refresh
`game-files/latest/` with a complete verified matched package and release assets,
then update the vault. Preserve the existing tested package until replacement
qualification. Source-only integration does not require a duplicate release.