# Controlled test NPC harness

This separate slice creates one temporary, tagged NPC in each game and mirrors the host actor's measured position/yaw at 10 Hz. It is **off by default**. Main source v0.0.37 now includes this harness behind `npc_test=false`; live deployment and verification belong to the main bench, not this standalone package. The existing `r6/scripts/CP2077Coop_npcsync` prototype is not required and must not be enabled for this test.

The intended first live check is an inert transform demonstration: spawn a generic actor, move its target a few metres from the test panel, observe the peer copy, then remove it. This does not provide autonomous shared AI, navigation/animation, combat, health/deaths, traffic, vanilla/quest NPC mirroring, loot or quest synchronization. Bounded AI teleport requests may look choppy. The first v0.0.35 live attempt failed: the joiner had the correct record and one attached, alive actor at world origin, despite falsely acknowledging creation. Main v0.0.36 added placement and engine-lifetime checks: both peers then matched initial actual placement, and by-ID removal was observed. The old facility teleport path failed to move the actor. One manual AITeleportCommand moved the host six metres, motivating the v0.0.37 bridge; continuous path and pause/reload checks remain pending.

## Contents and boundaries

- `scripts/CP2077Coop_testnpc/testnpc.reds` adds a private `CP2077Coop.TestNpc` actor tag and seven PlayerPuppet methods. It spawns only the fixed generic record `Character.spr_animals_bouncer1_ranged1_omaha_mb`, checks that the record exists, uses `persistState=false` and `persistSpawn=false`, and deletes only its own tag. It never accepts an entity ID/record/tag from the network, scans nearby NPCs, alters population/prevention, or wraps damage.
- `lua/testnpc.lua` owns the one-actor protocol and timeout state. It registers no CET handlers and never connects or polls a native socket. Caller callbacks own transport acceptance and entity operations. `cetEntity(Game.GetPlayer)` is an optional bridge to the seven methods; no game-object reference is retained in Lua across frames.
- `requirements-test.txt` supplies pytest and LuaJIT via lupa. Tests exercise the actual Lua module against mocked entity/transport operations; they do not model game physics.

The redscript attach callback disables perception for this tag; updates also set attitude toward the local player to neutral. That does not prove the NPC's entire AI is suspended. Inspect live for attacks, AI fighting transforms, animation or collision problems before expanding the slice. Stop/remove the test actor if any of those occur.

## Standalone integration contract and main adapter

Main source v0.0.37 exposes an optional session-bound extension API and `npc_test.lua` coordinates its opt-in handshake. Do not add another `Net_Poll`, call the old broad `CP2077Coop_NpcSetRole`, or paste a second `registerForEvent` handler. The existing event and transport owner calls the module after authenticating the opposite role and exchanging fresh session metadata. Main uses enableExtensions/extensionContext/sendExtension/takeExtension; its coordinator adds both app sessions, a fresh joiner challenge and a fresh host epoch before activation.

Create the module with `enabled=true` only after an explicit test feature flag is enabled in both games. Both sides must receive the **same freshly negotiated host harness epoch**; use a new epoch for every harness activation/reload/reconnect. Do not reuse an old epoch with a reset actor counter. `peer` is the native authenticated remote peer ID, not an ID parsed from an untrusted payload. Bind these values to the adapter's current connection generation and destroy the harness immediately when it changes.

```lua
local TestNpc = require("testnpc")
local actor = TestNpc.new({
    enabled = true,             -- actual integration must gate this explicitly
    role = localRole,           -- "host" or "joiner" from the local configuration
    epoch = agreedHostEpoch,    -- 1..40 decimal digits, fresh per activation
    peer = authenticatedPeerId,
    entity = TestNpc.cetEntity(Game.GetPlayer),
    send = function(reliable, message)
        -- Adapter wrapper: use sendExtension(currentContext, channel, message).
        -- Return true only after its bounded outbox/native accepted this payload.
        return sendTestNpcToCurrentPeer(reliable, message)
    end,
})
```

The adapter owner reserved **reliable channel 20** for lifecycle (`B/A/D/X`) and **unreliable channel 2** for pose (`S`). Preserve actual sender and reliability metadata and dispatch only to the current generation. Existing main assignments are movement=1, extra=16 and HELLO=30; do not reuse them. The main adapter dispatches these channels only after local opt-in through a bounded extension inbox; overflow disables the experiment while preserving player transport. `status().epoch` is a local generation counter, `status().session` is the local wire epoch, and `extensionContext()` includes both current sessions: none is automatically an agreed fresh harness epoch. Transport reliable delivery and this module's creation/removal ACK are different: accepting a reliable send does not establish that the game entity attached.

Call `actor:update(deltaTime)` once each frame. From the single receive dispatcher call `actor:receive(actualSender, isReliableChannel, payload)`. Only the host can invoke `actor:spawn({x=..., y=..., z=..., yaw=...})` and `actor:move(pose)`; position is in metres, yaw in degrees. The first call creates an ID scoped to the agreed epoch, and duplicate bind messages cannot create duplicate actors or rewind their pose.

Use `actor:stop()` to remove the host actor and retry a reliable despawn until the joiner acknowledges removal. Call `actor:shutdown()` before disconnect/reload/session end on **both** sides; it clears the local tag immediately and offers one best-effort remote removal. The joiner also removes a stale actor after three seconds without valid host traffic. Codeware `Session/BeforeEnd` independently clears the tag when game objects are still available. A stopped/stale ID becomes a tombstone: late bind/state messages cannot resurrect it. After a stale timeout, explicitly stop and spawn a fresh host actor; silent recovery of the expired ID is intentionally unsupported.

Creation requests are retried at most four times a second until accepted. Both attachment and measured position within 2 m of the immutable initial spawn request are required before movement, first bind or creation ACK; this has a five-second deadline, even if fresh states continue. Optional log callbacks emit request/placed/first-bind role, epoch, actor ID and pose once each. Cleanup of a previous actor also has a five-second spawn deadline; failure preserves cleanup tracking and cannot allow overlapping actors. Missing remote creation ACK causes the host to stop after ten seconds. Lifecycle messages retry at four Hz; pose sends are capped at ten Hz and are replaceable. No unbounded application outbox is created.

## Wire format

Every message is ASCII, at most 256 bytes: `NT1|epoch|kind|actorId|...`. `actorId` and pose sequence are positive 31-bit integers and never wrap; coordinates are finite and bounded to ±100000 m, yaw to ±360°. Only the expected peer/epoch is accepted.

| Kind | Channel | Fields after actorId | Direction |
|---|---|---|---|
| B | Reliable | x, y, z, yaw | Host bind/create |
| A | Reliable | none | Joiner confirms attached actor at initial requested pose |
| D | Reliable | none | Host remove |
| X | Reliable | none | Joiner confirms removal |
| S | Unreliable | sequence, x, y, z, yaw | Host actual sampled pose |

Wrong direction/channel/epoch/sender, malformed numeric fields, stale sequence numbers and old actor IDs are rejected. This protects the harness's authority boundary; it does not add authentication/encryption to the underlying transport.

## Validation and first live acceptance

As of 2026-10-04: **48 tests pass** (36 LuaJIT harness cases, 12 existing codec/probe cases). Compile against Baseline + Codeware + the main mod succeeded. The main mod's source v0.0.37 now includes an opt-in coordinator and this harness; live deployment/verification is owned by the main bench. It must not load the broad prototype folder beside it.

Codeware removes the tag registry before engine deletion completes. `entity.exists()` (optional for mocks; implemented by the CET bridge) therefore checks the private created EntityID, managed pending work, population spawning and retiring actor attachment. It does not equate an empty tag list with completed removal. Pending creation cancellation retains the ID and waits for an entity before deleting it. Removal ACK/new spawn wait for ownership cleanup; a cleanup timeout faults that attempted spawn but retains tracking. The lifecycle system is explicitly instantiated before spawn, and session teardown requests forced scoped cleanup. These source protections still need live removal verification.

```powershell
python -m pip install -r harness/requirements-test.txt
python -m pytest tests -q -p no:faulthandler
python D:/Downloads/syncfix/MP=Jakub/coop-tools/scc_check.py "G:/SteamLibrary/steamapps/common/Cyberpunk 2077 - Baseline" --scripts D:/Downloads/syncfix/coopnet/npcsync/harness/scripts
```

`-p no:faulthandler` avoids noisy Python Windows fault-handler reports for LuaJIT's handled structured exceptions; failed assertions still fail tests. Main's compiler helper now handles cross-drive script paths; the final harness compile exited 0 at 14:59 PDT on 2026-10-04.

Before calling this slice live-proven, record: exactly one actor per side; spawn matching record and pose; movement measured from each actual entity (not receive buffers); duplicate/reordered packets; refused send and loss recovery; despawn; three-second silence expiry; peer disconnect; main-menu/reload cleanup; zero remaining tag after cleanup; and unchanged vanilla population/prevention. Test on a backed-up non-quest bench save, with US and transatlantic link profiles. Keep full source/deployed hashes and logs. No frame-level accuracy claim is justified by the current mock tests.

## Primary API evidence

[Codeware documentation](https://github.com/psiberx/cp2077-codeware/wiki/) (version 1.18.0, checked 2026-10-04) documents record-based dynamic NPC spawning, tags, nonpersistent entity lifetime, and lifecycle callbacks. The harness uses those documented interfaces. `AITeleportCommand` follows the existing remote-avatar bridge and compiled against installed 2.31 scripts. The live single-command probe establishes one movement, not sustained path fidelity.

Codeware 1.18.0 implementation reference (checked 2026-10-04): [DynamicEntitySystem.cpp](https://github.com/psiberx/cp2077-codeware/blob/b1b2770cdf6ad2631666fb6ef4ccda99d864298e/src/App/World/DynamicEntitySystem.cpp), especially asynchronous CreateStub, DeleteEntity, GetEntity/IsSpawning and DeleteTagged. Initial-origin bind and pre-placement teleport interference remain hypotheses until the new transition logs are compared live.

## v0.0.37 movement bridge

The Lua module requests movement at most every 0.1 s and skips poses already within 0.02 m / 0.5 degrees (wrapped yaw). The redscript lifecycle owns one AITeleportCommand and weak actor reference; it refuses another while the command state is nonterminal. Lua polls MoveState and requests cancellation once after two seconds, then waits for terminal completion rather than overlapping commands. ClearOwned/session teardown cancels that handle. The host still transmits only measured positions. Diagnostics separate accepted movement requests from pending expiries; neither counts proven movement.

Optional entity callbacks `moveState()` and `cancelMove()` support asynchronous commands; mocks without them can represent synchronous movement. The real CET bridge always supplies them. Compilation and tests cover pacing, refusal, pending cancellation, terminal replacement and cleanup, but v0.0.37 needs its own live path/lifecycle test.
