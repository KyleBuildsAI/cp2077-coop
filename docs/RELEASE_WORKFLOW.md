# Every-version release workflow

Standing user instruction, 2026-10-04: every new version must update GitHub and
the local complete installation package. Apply this for Kyle, Codex, Claude Code
and other contributors. Routine commit/push, package refresh and publication of
the matching release assets are already authorized; no repeat approval is needed.

## Locations

- Canonical source: `D:\Downloads\syncfix\MP=Jakub`.
- GitHub: https://github.com/KyleBuildsAI/cp2077-coop.
- Local current package: `D:\Downloads\syncfix\MP=Jakub\game-files\latest`.
- Copy-into-game payload: `game-files/latest/game-root/`.
- Package specification/instructions: `game-files/latest/package-manifest.json`
  and `game-files/latest/README.md`.
- Packager: `scripts/package_game_files.py`.
- Vault: `G:\CyberpunkMP`, starting with `RESUME HERE.md` and `handoff.md`.

## Required completion steps

1. Identify the new gameplay/native/package versions and matching source revisions.
   Run the checks appropriate to the changes and retain results. Record game
   compilation, headless checks and live tests separately; keep known waivers visible.
2. Update the package manifest's file list, versions, input hashes and provenance,
   README, release/download links and any version-specific values in the packager
   (currently its archive name and fixed ZIP timestamp). Use the matching built and
   verified DLLs; never silently reuse a prior DLL for changed native source.
3. Build the complete package in a fresh staging folder with `--output`. Include
   required runtimes/licenses, matching Lua/redscript/native components, relay and
   safe configuration examples. Exclude personal room keys, roles, saves, logs,
   caches and local test switches. Verify every payload hash and ZIP contents;
   check reproducibility for the same inputs and manifest.
4. Preserve the previous package under a versioned archive before refreshing the
   generated files in `game-files/latest/`. Promote the verified staged payload,
   relay, ZIP and checksums together so the local folder contains the new version.
   The packager rejects mismatched existing files: do not bypass that guard by
   broadly deleting source files, vault notes or game installations. Check exact
   destinations before replacing generated output and leave no obsolete payloads.
5. Commit and push the matching source, instructions and manifest to the working
   branch; confirm the remote commit. Create a new versioned tag/release targeting
   the **full commit SHA**, and upload the complete ZIP, ZIP checksum and manifest.
   Keep experimental builds marked as prereleases. Use release assets for binaries;
   GitHub source archives alone are not the complete installation package.
6. Confirm GitHub assets finished uploading and their SHA256 digests match the
   local files. Preserve older tags/releases/assets; use a new package revision
   for changed bytes rather than silently replacing an existing release.
7. Update the vault's Status, handoff, RESUME and relevant version notes with the
   exact version, source commit, local folder, release link, checksums, checks run
   and remaining limitations. Report GitHub and local package locations to Kyle.

A version is complete only when source, local package, GitHub assets and vault
agree. If a check or upload fails, repair it and report the outstanding step;
keep the last verified package clearly identified instead of claiming completion.

This applies to each new mod/runtime or installation-package version, including
packaging-only revisions. Ordinary documentation/source-maintenance commits that
create no new runtime/package version still get committed/pushed, but do not
require a duplicate release of unchanged bytes. Packaging does not itself install
or launch a game; actual deployment retains the existing backup and test workflow.
