# CP2077 Coop — protocol v2 relay

Reference implementation of network protocol v2 for the CP2077 Coop mod: the relay server,
host/joiner test clients that act like the future game plugin, a v1 (Jakub's `CP1/RP1`) client
emulator, and the C++ wire header for the RED4ext plugin.

v2 replaces the v1 "one latest packet, extra state hidden in the forward-vector length" scheme with
a real session: handshake, rooms and roles, reliable ordered events, 30 Hz player snapshots
with timestamps, 10 Hz delta-compressed entity snapshots (NPCs, traffic, enemies) with interest
management, clock sync, and an interpolation buffer. The relay still serves v1 clients on the
same port, so the old DLL keeps working during migration.

## Requirements

- Python 3.11+ (standard library only, see `requirements.txt`)
- Optional: Visual Studio 2022 (MSVC) for `tools/check_c_header.py`

## Run

```bash
python -m unittest discover -s tests          # 89 unit tests (codecs, fuzzing, reliability, deltas, relay, test world)
python run_demo.py                            # 8 end-to-end scenarios over real UDP (about 5 minutes)
python run_demo.py --only realistic --duration 40
python run_demo.py --host-exe ../dllproto/build/Release/coopnet_v2_demo_client.exe   # C++ host, Python joiner
python tools/check_c_header.py                # compile include/coop_proto_v2.h, compare layouts and constants
python relay_v2.py                            # local relay on 127.0.0.1:11778 (v1 + v2)
python relay_v2.py --host :: --port 11778     # public relay, IPv6 + IPv4
python tools/relay_ping.py relay.example.net:11778   # RTT to a candidate v2 relay (no session created)
```

Scenarios: `clean`, `realistic` (host Russia-Warsaw, joiner LA-Warsaw), `stress`, `brutal` (20 % loss
and 5 % duplicates per leg), `bridge` (v2 host with a v1 client), and `course-clean`, `course-us`,
`course-transatlantic`. The course scenarios run both players over a scripted on-foot loop (walk, run,
sprint, turns, stops; `coopnet/testworld.py`) under end-to-end link profiles between the players:
clean, US (30 ±10 ms one way, 1 % loss) and transatlantic (115 ±20 ms, 1 % loss), split over the four
legs (uplink and downlink of each client). They report the interpolation error per kind of movement and
hold the plugin's relay clock estimator (C++ `ClockSync`) within 5 ms of the truth and of the other
client.

Every run ends with a drain: events stop 4 s before the end, each reliable stream ends with a
`"<name> done"` chat, and a client only quits once the relay acked all of its events and the other
player's marker arrived (at most 20 s later). A reliable violation is then a real loss, duplicate or
reordering, never a stream cut off while it was still being repaired. The relay exits one second after
the last v2 client left (`--exit-when-idle`).

`--host-exe` / `--joiner-exe` replace `client_v2.py` by any client that takes the same arguments and
writes the same report. The plugin repo's `coopnet_v2_demo_client.exe` (C++ on the plugin's protocol
modules) is driven through every scenario that way by its `tools/run_v2_demo.py`.

Manual session against a running relay (each client simulates its own link):

```bash
python client_v2.py --role host   --room demo --password pw --up-latency-ms 18 --down-latency-ms 18
python client_v2.py --role joiner --room demo --password pw --up-latency-ms 78 --down-latency-ms 78 --up-loss-pct 2
```

## Layout

| Path | Purpose |
|---|---|
| `coopnet/proto.py` | Wire format: header, handshake, every message codec and its validation (source of truth) |
| `coopnet/reliability.py` | Per-hop connection: packet seq/acks, RTT/RTO, reliable-ordered channel |
| `coopnet/snapshot.py` | Entity state quantization, delta encoder/decoder, interest manager, priorities |
| `coopnet/interp.py` | Clock sync with the relay, interpolation/extrapolation buffer, v1 behaviour model |
| `coopnet/legacy.py` | v1 text protocol and the init.lua forward-vector payload (for the bridge) |
| `coopnet/linksim.py`, `ratelimit.py`, `testworld.py` | Link impairment, token buckets, deterministic demo world and scripted course |
| `relay_v2.py` | The relay |
| `client_v2.py`, `legacy_client.py` | v2 test client (host/joiner), v1 client emulator |
| `run_demo.py` | Starts relay + clients as processes, checks the reports |
| `include/coop_proto_v2.h` | Packed C++ structs and constants for the plugin |
| `tools/` | Header checker, relay ping |
| `deploy/coop-relay.service` | systemd unit for a VPS |

## Protocol v2 in one page

**Datagram** (≤ 1200 bytes, little-endian): `magic CB 77 | major 2 | ptype | token u64 | seq u16 | ack u16 | ack_bits u32`
(20 bytes) + body. Byte 0 is not ASCII, so v2 and v1 text share UDP port 11778. Bytes 0–3 never change
across major versions, so any relay can answer a future HELLO with `REJECT(VERSION, min_minor, max_minor)`.

**Handshake**: `HELLO` (join info, zero-padded to 240 bytes) → `CHALLENGE` (40 bytes, stateless
HMAC cookie bound to IP:port, valid 10 s) → `AUTH` (join info + cookie + `SHA256("cp2077coop-v2|room|password")[:16]`)
→ `WELCOME` (peer id, role, 64-bit session token, negotiated minor, relay clock) or `REJECT(reason)`.
Join info carries protocol minor, requested role, capabilities, game build hash, mod version and a
u64 hash of the sorted mod list. The room creator can require identical mods/game build (`STRICT_MODS`).

**Minor versions**: the relay speaks 2.1 and accepts 2.0 clients. The negotiated minor is
`min(client, relay)`; WELCOME and PEER_JOINED carry it. 2.1 adds `SCRIPT_MSG` and the entity extension
`X_WORLD_ID`. The relay only accepts them from, and only delivers them to, peers that negotiated minor 1
(a 2.0 receiver is skipped and counted as `minor_filtered`).

**Channels** (per hop, client↔relay): each DATA packet acks the newest packet plus 32 before it.
Unreliable messages are sent once. Reliable messages carry a u16 message sequence, are resent after
an RTO (SRTT + 4·RTTVAR + 40 ms, exponential backoff) and delivered exactly once, in order. RTT samples
come only from acks carried by a packet that directly follows the previous one received: after a lost or
reordered packet the first ack through can cover packets whose earlier acks were lost, and their apparent
RTT includes the time the gap stayed open. A DATA packet holds at most 96 messages. Message header:
`type|0x80 if reliable, peer, length u16 [, rel_seq u16]` (4 or 6 bytes). Clients name the destination
peer; the relay rewrites it to the source peer.

| Type | Delivery | From → to | Size (bytes) | Rate |
|---|---|---|---|---|
| `PLAYER_SNAPSHOT` 0x10 | unreliable | player → all | 32 (+34 VehicleBlock while driving) | 30 Hz |
| `ENTITY_SNAPSHOT` 0x11 | unreliable | host → all | 14 + records, budget 1000 | 10 Hz |
| `SNAPSHOT_ACK` 0x12 | unreliable | player → host | 4 | with every packet |
| `FIRE_FX` 0x13 | unreliable | player → all | 24 | on fire |
| `TIME_REQ/RESP` 0x01/02 | unreliable | client ↔ relay | 4 / 12 | 10 Hz first 3 s, then 1 Hz |
| `PEER_JOINED/LEFT` 0x03/04 | reliable | relay → client | 24+name / 2 | on change |
| `LINK_STATS` 0x05 | unreliable | relay → client | 8 per member | 1 Hz |
| `EQUIP` 0x20 | reliable | player → all | 22 | on change |
| `VEHICLE_ENTER/EXIT` 0x21/22 | reliable | player → all | 36 / 18 | on change |
| `HIT` 0x23 | reliable | player → host (NPC) or victim (player) | 28 | per hit |
| `DEATH` 0x24 | reliable | owner → all | 12 | per death |
| `TIME_WEATHER` 0x25 | reliable | host → all | 18 | on change, ≥ every 30 s |
| `CHAT` 0x26 | reliable | player → all | 2+text (≤ 200) | ≤ 2/s |
| `TELEPORT_REQ/RESP` 0x27/28 | reliable | player → named peer | 4 / 18 | ≤ 1/s |
| `WORLD_FACT` 0x29 | reliable | host → all | 12 | quest facts |
| `MOD_LIST` 0x2A | reliable | player → all | 3+text chunks | on join |
| `SESSION_CONFIG` 0x2B | reliable | host → all | 8 | on join/change |
| `SCRIPT_MSG` 0x30 (2.1) | channel 1–15 unreliable, 16–31 reliable | player → named peer or all (0xFF) | 4+text (≤ 1000) | script bring-up |

**Script messages** (2.1): `channel u8, flags u8, text_len u16, text` with UTF-8 text of at most 1000 bytes and
no NUL; other control characters are allowed so scripts can send JSON. The channel numbers are the plugin's
`Net_Send` channels, and the reliable bit must match them (channels 16–31 reliable), otherwise the relay
drops the message as a violation. `flags` has no meaning yet and is relayed unchanged.

**Player snapshot**: sequence, `sample_time` (relay clock ms), position f32×3, yaw u16, pitch i16 (0.01°),
velocity i16×3 (cm/s), move state, health, flags (crouch, weapon drawn, aiming, firing, in vehicle, driving,
sprint, reload, 4-bit weapon class, dead, combat, legacy, teleported). While driving, a VehicleBlock adds the
vehicle's own position, smallest-three quaternion (pitch and roll included), linear and angular velocity,
steer/throttle/brake. This is what fixes "vehicles not aligned": v1 only had the player's position and a 2D
forward vector.

**Entity snapshot**: `tick, baseline_tick, sample_time, count` + records
`net_id u16, mask u8 [ext u8] [SPAWN kind/flags/attitude/TweakDBID/appearance 20] [POS i32×3 mm | POS_DELTA i16×3 mm]
[YAW u16 | QUAT u32] [VEL i16×3] [STATE move/flags/health 3] [TARGET u16] [WEAPON u64] [WORLD_ID u64]`.
`WORLD_ID` (ext bit 0x08, protocol 2.1) is the static EntityID hash of a placed NPC so the joiner can bind
its own copy (a "mirror"); it is part of the entity identity and travels with the spawn block. Records are deltas against
the newest snapshot the receiver acknowledged (`SNAPSHOT_ACK`); missing entities are unchanged, removals are
explicit, so a lost snapshot never breaks later ones. A walking NPC costs 9–17 bytes, a spawn 44. The host
runs interest management (NPCs within 100 m, vehicles within 200 m, +15 m hysteresis, hostile NPCs targeting
the player always, cap 128) and a priority accumulator (combat 3, vehicles/quest 2, crowd 1, closer = higher), so
the 1000-byte budget never starves anybody.

**Clock and interpolation**: clients sync to the relay clock NTP-style (lowest-RTT sample of the last 16,
then slew ≤ 2 ms per sample). A client sends no snapshots before it is synced, and resets its buffers'
timing when its offset steps. Receivers render at `relay_now − (fastest transit + delay)`, delay = clamp(jitter +
one interval, 100–150 ms) for players and 150–200 ms for 10 Hz entities. Position between samples is a Hermite
spline using the sent velocities; past the newest sample it is extrapolated for up to 250 ms, then held. New
entities stay hidden until render time reaches their first sample.

**v1 coexistence**: `CP1,...` datagrams get exactly the old behaviour (`WELCOME,<id>`, `RP1` forwarding between
v1 clients). A v2 room created with `LEGACY_BRIDGE` and named by `--legacy-room` (default `legacy`) is bridged
both ways: v1 players appear as peers 200+ with the `LEGACY` flag, and v2 snapshots go out as `RP1` with the
init.lua payload (flags, host bit, time/weather, pong replies to v1 pings).

## Deploying a public relay

Player-to-player latency is set by the Moscow ↔ Los Angeles fibre path (roughly 170–200 ms RTT). A relay *on*
that path (Europe → transatlantic → US East → US West) adds almost nothing. A relay off the path adds the detour.
The estimates below come from fibre distance and typical routing, not measurements. Measure with
`tools/relay_ping.py` from both PCs before you choose.

| Relay | Russia leg RTT | Los Angeles leg RTT | Notes |
|---|---|---|---|
| Frankfurt / Amsterdam VPS | 40–60 ms | 140–160 ms | Best peering (DE-CIX/AMS-IX), cheap (Hetzner, OVH, Vultr, DO) |
| Warsaw (Jakub's, OVH) | 25–45 ms | 150–170 ms | Fine latency, but the code and uptime are not under your control |
| US East (Ashburn / New York) | 110–140 ms | 60–75 ms | Similar total; splits the legs more evenly (faster resends on the worst leg) |
| Russia or US West | — | — | Avoid: one player gets the whole path twice |

Recommended: a small Frankfurt VPS (1 vCPU is plenty; a session uses about 10 KB/s per player). Keep a
US-East instance as the alternative if the Russian ISP routes poorly to Frankfurt. Some Russian ISPs throttle or
block hosting ranges and unusual UDP. If 11778/udp is filtered, also expose the relay on 443/udp
(`iptables -t nat -A PREROUTING -p udp --dport 443 -j REDIRECT --to-port 11778`).

Setup: `deploy/coop-relay.service` (systemd, unprivileged user, `--host ::` for dual-stack IPv4/IPv6), open
UDP 11778 in `ufw` and in the provider's firewall.

**NAT**: players never need port forwarding. The client opens the mapping by sending first and keeps it alive
(30 Hz in game, 1 Hz keepalive in menus; carrier-grade NAT can drop idle mappings after ~30 s). The relay
identifies a session by its token, not by IP:port, so a NAT rebinding (new public port) is followed
automatically when the packet is newer than the last one. Datagrams stay ≤ 1200 bytes, so PPPoE, VPN and IPv6
paths never fragment.

## Security

- The relay never executes or interprets content beyond strict, length-checked binary parsing. Unknown types,
  wrong reliability bit, non-finite floats, out-of-world coordinates (|x|,|y| ≤ 20 km, |z| ≤ 5 km), bad enums,
  invalid UTF-8 and control characters in names/chat are dropped and counted. More than 200 violations in
  10 s gets a client kicked.
- Role enforcement: only the host may send `ENTITY_SNAPSHOT`, `TIME_WEATHER`, `WORLD_FACT` and `SESSION_CONFIG`.
  Relay-only types from clients are violations. Spectators may only chat and ack.
- Rate limits per connection: 120 packets/s, 64 KB/s, 60 reliable messages/s, chat 2/s, teleport 1/s;
  handshakes 4/s per IP. A client that lets its 256-message reliable window fill up is disconnected.
  The reliable, chat and teleport limits are charged when a reliable message first arrives, not when it
  is routed. Messages that arrived behind a lost packet are released together once the gap is repaired;
  they were acked on arrival, and charging them on release dropped part of a correctly paced burst.
- Anti-spoofing/amplification: stateless cookie, HELLO ≥ 240 bytes vs 40-byte CHALLENGE. Room passwords are
  never sent (16-byte salted hash). Session tokens are 64-bit random.
- Not encrypted: an on-path attacker can read traffic and replay a token. That is acceptable for two friends.
  If needed later, a minor version can add X25519 + ChaCha20-Poly1305 per packet.
- The plugin must also validate: TweakDBIDs from the network are spawned only if they exist and are of the
  expected record type (character/vehicle/item). Network strings never reach Lua `load` or the CET console.

## Rules for the game plugin (learned from the runs)

1. Do not send snapshots before the relay clock is synced (first 3 `TIME_RESP`), and do not feed received
   samples into buffers before your own sync. Early unsynced timestamps made the joiner see the host jump
   for seconds before this rule existed.
2. Process DATA packets that arrive in the same batch as the WELCOME (first `PEER_JOINED`).
3. Keep a per-sender sample buffer in the plugin, not a single "latest packet" slot. With the realistic
   RU/LA link, the v1 model (latest packet + exponential follow) is 5–16 cm off at p95 with spikes to 0.5–0.95 m
   and 10× the acceleration noise. The v2 buffer stays within 6–14 mm.
4. Vehicles: send the vehicle's own pose (VehicleBlock), and drive the remote vehicle as a kinematic proxy.
5. World NPCs: the joiner must suppress its own crowd/traffic/spawners (`SESSION_CONFIG.joiner_population = 0`)
   and spawn proxies from `ENTITY_SNAPSHOT`. The host stays the authority (`HIT` → host applies damage →
   state and `DEATH` flow back).
