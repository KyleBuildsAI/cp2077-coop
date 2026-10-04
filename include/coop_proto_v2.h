#pragma once

// CP2077 Coop network protocol v2 - wire layout for the RED4ext plugin (C++17).
//
// Mirrors relay/coopnet/proto.py, which is the single source of truth.
// tools/check_c_header.py compiles this header with MSVC and verifies every
// struct size and field offset against the Python definitions.
//
// All fields are little-endian (x64 native). Structs are packed: copy them
// out of a datagram with memcpy, never by casting an unaligned pointer on
// another architecture.
//
// Datagram = PacketHeader (20) + body. DATA bodies are a sequence of
// MessageHeader (4) [+ uint16 reliable sequence when type & kReliableBit] + payload.
// Text fields follow their fixed struct as uint8 length + UTF-8 bytes
// (no control characters): PeerJoined.name, Chat.text, ModList.text, Reject.text.

#include <cstdint>

namespace coopv2
{
constexpr uint8_t kMagic0 = 0xCB;
constexpr uint8_t kMagic1 = 0x77;
constexpr uint8_t kProtoMajor = 2;
constexpr uint8_t kProtoMinor = 0;
constexpr uint8_t kMinSupportedMinor = 0;
constexpr uint16_t kMaxPacket = 1200;
constexpr uint16_t kHelloMinPacket = 240;     // HELLO is zero-padded to at least this size
constexpr uint8_t kReliableBit = 0x80;
constexpr uint8_t kPeerRelay = 0;
constexpr uint8_t kPeerBroadcast = 0xFF;
constexpr uint8_t kLegacyPeerBase = 200;      // bridged v1 players use peer ids 200..254
constexpr uint16_t kPlayerTargetBase = 0xFF00; // entity target field: 0xFF00 + peer id = a player
constexpr float kWorldXYLimit = 20000.0f;
constexpr float kWorldZLimit = 5000.0f;

enum class PacketType : uint8_t
{
    Hello = 1,
    Challenge = 2,
    Auth = 3,
    Welcome = 4,
    Reject = 5,
    Data = 6,
    Disconnect = 7,
};

enum class Role : uint8_t
{
    Any = 0,
    Host = 1,
    Joiner = 2,
    Spectator = 3,
};

enum class RejectReason : uint8_t
{
    Version = 1,
    BadCookie = 2,
    RoomFull = 3,
    BadKey = 4,
    RoleTaken = 5,
    ModMismatch = 6,
    RateLimited = 7,
    ServerFull = 8,
    Malformed = 9,
    GameBuild = 10,
};

enum class DisconnectReason : uint8_t
{
    Quit = 1,
    Timeout = 2,
    Kicked = 3,
    RateLimit = 4,
    ProtocolError = 5,
    ServerShutdown = 6,
    SlowConsumer = 7,
};

enum JoinFlag : uint16_t
{
    kJoinStrictMods = 0x01,
    kJoinLegacyBridge = 0x02,
};

enum Cap : uint32_t
{
    kCapPlayer = 0x001,
    kCapEntities = 0x002,
    kCapVehicles = 0x004,
    kCapCombat = 0x008,
    kCapWorldState = 0x010,
    kCapChat = 0x020,
    kCapTeleport = 0x040,
    kCapLegacyBridge = 0x080,
    kCapQuestFacts = 0x100,
};

// Delivery is fixed per type: the relay drops a message whose reliable bit disagrees.
enum class MsgType : uint8_t
{
    TimeReq = 0x01,        // unreliable, client -> relay
    TimeResp = 0x02,       // unreliable, relay -> client
    PeerJoined = 0x03,     // reliable,   relay -> client
    PeerLeft = 0x04,       // reliable,   relay -> client
    LinkStats = 0x05,      // unreliable, relay -> client, 1 Hz per room member
    PlayerSnapshot = 0x10, // unreliable, 30 Hz, broadcast
    EntitySnapshot = 0x11, // unreliable, 10 Hz, host only, broadcast
    SnapshotAck = 0x12,    // unreliable, -> host
    FireFx = 0x13,         // unreliable, broadcast
    Equip = 0x20,          // reliable ordered events from here on
    VehicleEnter = 0x21,
    VehicleExit = 0x22,
    Hit = 0x23,            // -> host (target_kind 0) or the victim player (target_kind 1)
    Death = 0x24,
    TimeWeather = 0x25,    // host only, cached by the relay for late joiners
    Chat = 0x26,           // rate limited 2/s, burst 5
    TeleportReq = 0x27,    // -> peer named in MessageHeader.peer
    TeleportResp = 0x28,   // -> peer named in MessageHeader.peer
    WorldFact = 0x29,      // host only, cached
    ModList = 0x2A,        // text "name@version;name@version" in chunks of <= 255 bytes
    SessionConfig = 0x2B,  // host only, cached
};

enum class MoveState : uint8_t
{
    Idle = 0, Walk, Run, Sprint, CrouchIdle, CrouchMove, Jump, Fall, Slide, Swim, Vehicle, Dead, Ladder, Dodge,
};

enum PlayerFlag : uint16_t
{
    kPlayerCrouch = 1 << 0,
    kPlayerWeaponDrawn = 1 << 1,
    kPlayerAiming = 1 << 2,
    kPlayerFiring = 1 << 3,
    kPlayerInVehicle = 1 << 4,
    kPlayerDriving = 1 << 5,      // a VehicleBlock follows the PlayerSnapshot
    kPlayerSprinting = 1 << 6,
    kPlayerReloading = 1 << 7,
    kPlayerWeaponClassShift = 8,  // bits 8..11
    kPlayerWeaponClassMask = 0xF,
    kPlayerDead = 1 << 12,
    kPlayerInCombat = 1 << 13,
    kPlayerLegacy = 1 << 14,      // bridged from v1: no velocity, relay timestamp
    kPlayerTeleported = 1 << 15,  // do not interpolate across this sample
};

enum class EntityKind : uint8_t
{
    CrowdNpc = 1,
    CombatNpc = 2,
    QuestNpc = 3,
    Vehicle = 4,
    Device = 5,
};

enum EntityFlag : uint8_t
{
    kEntityDead = 0x01,
    kEntityCombat = 0x02,
    kEntityWeaponDrawn = 0x04,
    kEntityCrouched = 0x08,
    kEntityRagdoll = 0x10,
    kEntityHostile = 0x20,
    kEntityLights = 0x40,
    kEntitySiren = 0x80,
};

// Entity record: net_id:u16 mask:u8 [ext:u8] [Spawn 20] [pos i32x3 mm | pos_delta i16x3 mm]
// [yaw u16 | quat u32] [vel i16x3 cm/s] [state u8 move, u8 flags, u8 health] [target u16] [weapon u64]
enum EntityMask : uint8_t
{
    kMaskSpawn = 0x01,
    kMaskPos = 0x02,
    kMaskPosDelta = 0x04,
    kMaskYaw = 0x08,
    kMaskQuat = 0x10,
    kMaskVel = 0x20,
    kMaskState = 0x40,
    kMaskExt = 0x80,
};

enum EntityExt : uint8_t
{
    kExtRemove = 0x01, // alone: the entity left the snapshot (despawn / out of interest)
    kExtTarget = 0x02,
    kExtWeapon = 0x04,
};

#pragma pack(push, 1)

struct PacketHeader
{
    uint8_t magic[2];
    uint8_t major;
    uint8_t ptype;
    uint64_t token;    // 0 during the handshake
    uint16_t seq;      // DATA: per-hop packet sequence, never 0
    uint16_t ack;      // newest packet seen from the other side (0 = none)
    uint32_t ack_bits; // bit i => packet (ack - 1 - i) was also received
};

struct MessageHeader
{
    uint8_t type; // MsgType | kReliableBit
    uint8_t peer; // client -> relay: destination; relay -> client: source peer (0 = relay)
    uint16_t length;
};

struct JoinFixed // HELLO and AUTH body start; then u8 room_len + room, u8 name_len + name
{
    uint8_t minor;
    uint8_t role;
    uint16_t join_flags;
    uint32_t caps;
    uint32_t game_build;   // FNV-1a 32 of the game version string, e.g. "2.31a"
    uint16_t mod_major;
    uint16_t mod_minor;
    uint16_t mod_patch;
    uint64_t mod_hash;     // first 8 bytes of SHA-256 over sorted "name@version" lines
    uint16_t mod_count;
    uint64_t client_nonce;
    uint64_t resume_token; // previous session token to reclaim the same peer id, else 0
};
// AUTH body = JoinFixed + room + name + cookie[16] + key_hash[16]
// key_hash = SHA-256("cp2077coop-v2|" + room + "|" + password)[0..16)

struct Challenge
{
    uint8_t minor_min;
    uint8_t minor_max;
    uint16_t reserved;
    uint8_t cookie[16];
};

struct Welcome
{
    uint8_t minor; // negotiated = min(client, relay)
    uint8_t peer_id;
    uint8_t role;
    uint8_t room_flags;
    uint64_t token;
    uint32_t relay_time_ms;
    uint8_t player_hz;
    uint8_t entity_hz;
    uint16_t max_packet;
    uint32_t room_caps;
};

struct RejectFixed // then text_len bytes of UTF-8
{
    uint8_t reason;
    uint8_t minor_min;
    uint8_t minor_max;
    uint8_t text_len;
};

struct TimeReq
{
    uint32_t t0; // client clock, ms
};

struct TimeResp
{
    uint32_t t0; // echoed
    uint32_t t1; // relay clock at receive, ms
    uint32_t t2; // relay clock at send, ms
};

struct PeerJoined // then u8 name_len + name
{
    uint8_t peer_id;
    uint8_t role;
    uint8_t minor;
    uint8_t peer_flags; // 0x01 = bridged v1 client
    uint32_t caps;
    uint64_t mod_hash;
    uint16_t mod_count;
    uint16_t mod_major;
    uint16_t mod_minor;
    uint16_t mod_patch;
};

struct PeerLeft
{
    uint8_t peer_id;
    uint8_t reason;
};

struct LinkStats
{
    uint8_t peer_id;
    uint8_t reserved;
    uint16_t rtt_ms;            // relay <-> that peer
    uint16_t loss_in_permille;  // packets from that peer lost on the way to the relay
    uint16_t loss_out_permille; // relay packets to that peer lost
};

struct PlayerSnapshot
{
    uint16_t snap_seq;
    uint32_t sample_time; // relay clock (ms) when sampled
    float x;
    float y;
    float z;
    uint16_t yaw;      // degrees * 65536 / 360
    int16_t pitch;     // centidegrees, +-9000
    int16_t vx;        // cm/s
    int16_t vy;
    int16_t vz;
    uint8_t move_state;
    uint8_t health;    // 0..255
    uint16_t flags;    // PlayerFlag
};

struct VehicleBlock // follows PlayerSnapshot when flags & kPlayerDriving
{
    uint16_t vehicle_net;
    float px;
    float py;
    float pz;
    uint32_t quat;     // smallest-three: 2-bit index of dropped component + 3 x 10 bits
    int16_t lvx;       // cm/s
    int16_t lvy;
    int16_t lvz;
    int16_t avx;       // mrad/s
    int16_t avy;
    int16_t avz;
    int8_t steer;      // -100..100
    uint8_t throttle;  // 0..100
    uint8_t brake;     // 0..100
    uint8_t vflags;
};

struct SnapshotAck
{
    uint32_t tick; // newest entity snapshot tick fully decoded
};

struct FireFx
{
    uint32_t time_ms;
    int32_t ox; // mm
    int32_t oy;
    int32_t oz;
    int16_t dx; // unit vector * 32767
    int16_t dy;
    int16_t dz;
    uint8_t weapon_class;
    uint8_t shots;
};

struct Equip
{
    uint8_t slot;
    uint8_t weapon_class;
    uint16_t flags;
    uint64_t item_record; // TweakDBID
    uint64_t appearance;
    uint16_t ammo;
};

struct VehicleEnter
{
    uint16_t vehicle_net;
    uint8_t seat;
    uint8_t flags;
    uint64_t record;     // TweakDBID of the vehicle record
    uint64_t appearance; // CName hash
    float x;
    float y;
    float z;
    uint32_t quat;
};

struct VehicleExit
{
    uint16_t vehicle_net;
    uint8_t seat;
    uint8_t flags;
    float x;
    float y;
    float z;
    uint16_t yaw;
};

struct Hit
{
    uint16_t target_net;
    uint8_t target_kind; // 0 entity (host applies), 1 player (victim applies)
    uint8_t hit_zone;
    uint8_t attack;
    uint8_t flags;
    float damage;
    uint64_t weapon_record;
    int16_t rx; // hit point relative to target origin, cm
    int16_t ry;
    int16_t rz;
    uint32_t time_ms; // relay clock
};

struct Death
{
    uint16_t target_net;
    uint8_t target_kind;
    uint8_t cause;
    uint16_t killer_net;
    uint8_t killer_kind; // 0 entity, 1 player, 2 world
    uint8_t flags;
    uint32_t time_ms;
};

struct TimeWeather
{
    uint32_t game_seconds;
    uint16_t time_scale_x100;
    uint8_t flags;
    uint8_t weather_id;
    uint64_t weather_record;
    uint16_t transition_s;
};

struct ChatFixed // then u8 text_len + text (<= 200 bytes)
{
    uint8_t channel;
};

struct TeleportReq
{
    uint16_t req_id;
    uint8_t mode; // 0 bring me to you, 1 summon you to me
    uint8_t reserved;
};

struct TeleportResp
{
    uint16_t req_id;
    uint8_t accepted;
    uint8_t reserved;
    float x;
    float y;
    float z;
    uint16_t yaw;
};

struct WorldFact
{
    uint64_t fact_hash;
    int32_t value;
};

struct ModListFixed // then u8 text_len + text
{
    uint8_t chunk;
    uint8_t chunks;
};

struct SessionConfig
{
    uint16_t npc_radius_m;
    uint16_t vehicle_radius_m;
    uint8_t entity_hz;
    uint8_t joiner_population; // 0 = joiner suppresses its own crowd/traffic/spawners
    uint16_t flags;
};

struct EntitySnapshotHeader
{
    uint32_t tick;
    uint32_t baseline;    // 0 = full snapshot, else a tick the receiver acked
    uint32_t sample_time; // relay clock, ms
    uint16_t count;
};

struct EntitySpawn
{
    uint8_t kind;
    uint8_t spawn_flags;
    uint8_t attitude;
    uint8_t reserved;
    uint64_t record;     // TweakDBID (character / vehicle record)
    uint64_t appearance; // CName hash
};

#pragma pack(pop)

static_assert(sizeof(PacketHeader) == 20, "PacketHeader");
static_assert(sizeof(MessageHeader) == 4, "MessageHeader");
static_assert(sizeof(JoinFixed) == 44, "JoinFixed");
static_assert(sizeof(Challenge) == 20, "Challenge");
static_assert(sizeof(Welcome) == 24, "Welcome");
static_assert(sizeof(RejectFixed) == 4, "RejectFixed");
static_assert(sizeof(TimeReq) == 4, "TimeReq");
static_assert(sizeof(TimeResp) == 12, "TimeResp");
static_assert(sizeof(PeerJoined) == 24, "PeerJoined");
static_assert(sizeof(PeerLeft) == 2, "PeerLeft");
static_assert(sizeof(LinkStats) == 8, "LinkStats");
static_assert(sizeof(PlayerSnapshot) == 32, "PlayerSnapshot");
static_assert(sizeof(VehicleBlock) == 34, "VehicleBlock");
static_assert(sizeof(SnapshotAck) == 4, "SnapshotAck");
static_assert(sizeof(FireFx) == 24, "FireFx");
static_assert(sizeof(Equip) == 22, "Equip");
static_assert(sizeof(VehicleEnter) == 36, "VehicleEnter");
static_assert(sizeof(VehicleExit) == 18, "VehicleExit");
static_assert(sizeof(Hit) == 28, "Hit");
static_assert(sizeof(Death) == 12, "Death");
static_assert(sizeof(TimeWeather) == 18, "TimeWeather");
static_assert(sizeof(ChatFixed) == 1, "ChatFixed");
static_assert(sizeof(TeleportReq) == 4, "TeleportReq");
static_assert(sizeof(TeleportResp) == 18, "TeleportResp");
static_assert(sizeof(WorldFact) == 12, "WorldFact");
static_assert(sizeof(ModListFixed) == 2, "ModListFixed");
static_assert(sizeof(SessionConfig) == 8, "SessionConfig");
static_assert(sizeof(EntitySnapshotHeader) == 14, "EntitySnapshotHeader");
static_assert(sizeof(EntitySpawn) == 20, "EntitySpawn");
} // namespace coopv2
