# CP2077 Coop

Experimental two-player co-op for Cyberpunk 2077. Current tested build: **v0.0.37 + CP2077CoopNet 0.2.0-alpha.5**, for Cyberpunk **2.31** (executable file version 3.0.80.51928).

## Download v37 / alpha.5

### [Download the complete mod ZIP (39 MB)](https://github.com/KyleBuildsAI/cp2077-coop/releases/download/v0.0.37-game-bundle.1/CP2077Coop-v0.0.37-alpha5-game-files.zip)

Includes the co-op scripts, both co-op DLLs, required mod runtimes and matching relay.
**Use the download above, not GitHub's green Code > Download ZIP or Source code archives:** those contain source, not the complete installation files.

[Release details](https://github.com/KyleBuildsAI/cp2077-coop/releases/tag/v0.0.37-game-bundle.1) · [Full installation guide](game-files/latest/README.md) · [ZIP checksum](https://github.com/KyleBuildsAI/cp2077-coop/releases/download/v0.0.37-game-bundle.1/CP2077Coop-v0.0.37-alpha5-game-files.zip.sha256)

This is an **experimental prerelease**. Player movement still needs smoothing; cars are cosmetic replicas, and shared driver/passenger seats are unfinished. See the [known issues and requested fixes](docs/PLAYTEST_V37_BACKLOG.md). Both players should download the same package.

The source tree now includes the native plugin (`plugin/`), relay (`relay/`), NPC
harness and measurement tools with their Git histories preserved. Start with
[shared development](docs/DEVELOPMENT.md), [integration provenance](docs/INTEGRATION_PROVENANCE.md)
and the [multiplayer roadmap](docs/MULTIPLAYER_PLAN.md). The deployed gameplay
baseline is still v0.0.37/alpha.5; build infrastructure and the opt-in headless
authority experiment do not constitute a new complete multiplayer release.

The [authority experiment](relay/docs/AUTHORITY_EXPERIMENT.md) adds host-approved
vehicle/seat metadata, baseline readiness and stale-session rejection over the
existing CB77 protocol. It is disabled by default and verified with actual UDP
clients under loss and reconnect. Connecting it to observed game mounting and
driving remains a later roadmap stage.

Each player sends their position, facing and gameplay state to a relay server over UDP.
The other player is shown as a stand-in NPC that copies that movement and state.

## Requirements

All of these ship inside the release zip:

- [Cyber Engine Tweaks](https://www.nexusmods.com/cyberpunk2077/mods/107) (Lua bridge)
- [RED4ext](https://www.nexusmods.com/cyberpunk2077/mods/2380) (loads the network plugin)
- [redscript](https://www.nexusmods.com/cyberpunk2077/mods/1511) (script compiler)
- [Codeware](https://www.nexusmods.com/cyberpunk2077/mods/7780) 1.18.0 (entity spawning, hiding the avatar while the other player drives)

## Install

1. **Download and extract** the complete mod ZIP linked above into a separate folder. Close the game and back up your saves and any mod files you will replace.
2. **Copy the contents of `game-root`** (`bin`, `engine`, `r6`, `red4ext`) into your Cyberpunk game folder, merging with its existing folders. Do not copy the enclosing `game-root` folder itself.
3. **Configure the connection.** For a fresh installation, copy the extracted `config-examples/transport.ini.example` to `bin/x64/plugins/cyber_engine_tweaks/mods/CP2077Coop/transport.ini` in the game folder. Set the relay address, port, shared room and your chosen room key. `127.0.0.1` works only when the relay runs on the same computer. Existing installations keep their own configuration; check it matches your intended relay.
4. **Run the included relay** from the extracted package using Python 3.12 or newer: `python relay/relay_v2.py --host 0.0.0.0 --port 11778`. Both players must be able to reach that computer over UDP on port 11778.
5. **Launch both games** and choose HOST on one and JOINER on the other in the CET **CP2077 Coop** panel. Keep NPC and retained-steering experiments off. Follow the [full installation guide](game-files/latest/README.md) for probe compatibility, configuration and rollback details.

**Both players must run the same version**: the panel title (*CP2077 Coop v0.0.37*), the
*Version* row at the top of the panel and `version=` at the start of every `[STATS]` line show it.
The protocol changes between builds: a partner on an older build shows up as *Peer: no ping reply -
other player on old version?* and *Role check: unknown*, not as a role problem. Update the older
side instead of switching roles. Against a v0.0.26 or v0.0.27 partner a current build still shows
position and facing, but the RTT and the role check stay empty.

## Run

Start the relay, then launch both games and choose HOST on one and JOINER on the other. The **CP2077 Coop** panel shows connection status and ping; toggle it through CET **Bindings → Toggle coop panel**.

## Version history

- **v0.0.37 / alpha.5 (current):** Fixed controlled test-NPC movement; complete installation ZIP available.
- **v0.0.36:** Added checks for actual test-NPC spawn placement and improved cleanup.
- **v0.0.35:** Added an optional test NPC and easier-to-reach co-op controls.
- **v0.0.34:** Added experimental native v2 networking with legacy fallback.
- **v0.0.33:** Added a partner marker on the map and minimap.
- **v0.0.32:** Improved joining and state updates; displayed the build version in diagnostics.
- **v0.0.31:** Added legacy combat-hit sharing, ping and expanded diagnostics.
- **v0.0.30:** Added cosmetic replicas of the other player's vehicle.
- **v0.0.29:** Improved remote-player movement with persistent movement commands.
- **v0.0.28:** Fixed repeated join teleports and blocked them while driving.
- **v0.0.27:** Added crouch, weapon, time and weather synchronization.
- **v0.0.26:** Established the initial player-position synchronization baseline.

[Technical reference](docs/RUNTIME_REFERENCE.md) · [Known issues](docs/PLAYTEST_V37_BACKLOG.md) · [Roadmap](docs/MULTIPLAYER_PLAN.md)
