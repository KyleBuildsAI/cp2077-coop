# Latest complete game installation files

**Experimental v0.0.37 + CP2077CoopNet 0.2.0-alpha.5**, for the tested Cyberpunk
2.31 executable (file version 3.0.80.51928).

[Download the complete installation ZIP](https://github.com/KyleBuildsAI/cp2077-coop/releases/download/v0.0.37-game-bundle.1/CP2077Coop-v0.0.37-alpha5-game-files.zip)

This folder is the package entry point. Download the release ZIP for **all** files,
including DLLs and third-party runtimes. GitHub's source-code ZIP does not contain
those ignored binaries. Locally, the generated `game-root/` is ready to copy.

## Install or update

1. Close Cyberpunk. Back up your saves and any mod files you will replace.
2. Extract the download into a separate folder. Copy the **contents of `game-root`**
   (`bin`, `engine`, `r6`, `red4ext`) into your Cyberpunk installation, beside its
   existing folders. Do not put the enclosing `game-root` folder inside the game.
3. For an existing co-op installation, retain your `transport.ini`, `role.txt` and
   `server.ini`. This package does not include personal configuration or overwrite
   those files. It also excludes saves, game archives, logs and generated caches.
4. For a fresh install, copy `config-examples/transport.ini.example` to
   `bin/x64/plugins/cyber_engine_tweaks/mods/CP2077Coop/transport.ini` and edit it.
   Both players need the same relay port, room and chosen room key. Set `host` to
   the reachable relay computer's address; `127.0.0.1` only works on that computer.
   Do not leave the example key unchanged. The optional legacy `server.ini.example`
   belongs under `red4ext/plugins/CP2077Coop/server.ini` if using the older v1 mode.
5. Disable any separately installed NetProbe/CoopNetCheck polling mod before v2.
   The package includes the current CoopNetCheck `disabled.txt` gate, but older
   probes may need their `init.lua` renamed to `init.lua.disabled`. Only the main
   co-op mod may own `Net_Poll`. Set `probe_disabled=true` only after checking this.
6. Start the relay from the extracted package with Python 3.12 or newer:
   `python relay/relay_v2.py --host 0.0.0.0 --port 11778`.
   The relay computer must be reachable over UDP on that port. It runs separately
   from the game. Leave `--entity-authority-test` off for gameplay.
7. Launch each game, open the CET co-op panel and choose HOST on one and JOINER on
   the other. Use matching packages. Keep `npc_test=false` and
   `native_retarget=false` for the normal baseline.

If updating an installation with newer third-party runtimes, review their versions
before overwriting them: this bundle deliberately pins the tested combination.
For rollback, restore your backed-up files; do not delete shared runtime folders
that other mods use. No installer or game is launched by extracting this package.

## Included and verified

- Current four co-op Lua modules and eight game redscripts (v0.0.37).
- Original CP2077Coop.dll plus the exact game-tested alpha.5 CP2077CoopNet.dll and
  its two plugin-loaded scripts. Do not duplicate those declarations in `r6/scripts`.
- CET 1.37.1, RED4ext 1.30.0, Codeware 1.18.0, the tested redscript compiler,
  ASI loader, required runtime data/configuration, and license notices.
- Matching current Python relay, safe configuration examples and SHA256 manifest.

`package-manifest.json` pins every payload file and its provenance. Gameplay and
native runtime bytes match the October 4 v37 bench; packaging does not add new
gameplay. Source integration passed [four CI jobs](https://github.com/KyleBuildsAI/cp2077-coop/actions/runs/37259471154).
The sprint-lag waiver remains open; vehicle authority is still a headless
experiment, and this is not a complete shared-world or campaign release.

## Rebuild the identical package

From the repository root, provide the matching runtime dependency bundle and the
bench alpha.5 DLL. Neither is recovered from an arbitrary live installation by
the packager. All input hashes are checked before any output is written.

```powershell
python scripts/package_game_files.py --bundle-root PATH_TO_MATCHING_DEPENDENCIES --native-plugin PATH_TO_BENCH_ALPHA5_DLL
```

The default output is this folder's ignored `game-root/`, `relay/` and installation
ZIP. `--output` selects another empty output folder. Generated copies are not
independent development sources; edit the canonical repository, then deliberately
update the package version/manifest for the next tested batch.
