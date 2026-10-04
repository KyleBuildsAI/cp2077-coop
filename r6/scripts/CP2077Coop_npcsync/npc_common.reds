// CP2077Coop NPC / world sync - shared definitions (host and joiner).
//
// Host-authoritative model:
//   * The host enumerates NPCs and traffic around BOTH players, gives each a
//     16-bit netId and streams quantized state at 10 Hz.
//   * Placed/community NPCs have STATIC EntityIDs (hash of the world node ref,
//     identical in two games that loaded the same save on the same build).
//     The joiner binds those netIds to its own local copy ("mirror").
//   * Crowd, traffic and runtime spawns have DYNAMIC EntityIDs (<= 0xFFFFFF,
//     a per-session counter) that differ between games. The joiner suppresses
//     its own random population and spawns a proxy from record + appearance.
//
// Enum values match relay/coopnet/proto.py (EntityKind, EntityFlag, SpawnFlag,
// MoveState) so the text messages below map 1:1 onto the binary v2
// ENTITY_SNAPSHOT record once the plugin encodes it natively.
//
// Text wire format v1 (payload of Net_Send / Net_Poll, ASCII, fields split by
// a single space, entries by ';', entry fields by ','). Integers only.
//
//   NS1 <seq> <hostMs> <anchorX> <anchorY> <anchorZ> <part> <parts> <entries>     unreliable
//       entry = netId,dxCm,dyCm,dzCm,yaw16,vxCms,vyCms,moveState,flags,health,target
//       anchor = host player position rounded to whole metres
//   NB1 <netId> <kind> <spawnFlags> <attitude> <recHash> <recLen> <appearance> <worldId> <xCm> <yCm> <zCm> <yaw16>   reliable
//       recHash/recLen = TweakDBID name hash (u32) and name length (u8)
//       appearance = CName hash (u64), worldId = static EntityID hash (u64, 0 = dynamic)
//   NU1 <netId> <reason>                                   reliable (0 out of range, 1 despawned)
//   ND1 <netId> <killer> <flags>                           reliable (flags: 1 ragdoll, 2 skip death anim)
//   NI1 <seq> <cxM> <cyM> <czM> <radiusM> <netId;netId;...>   unreliable, 1 Hz coverage index
//   NH1 <netId> <damageX100> <attackType> <hitFlags>       reliable, joiner -> host
//   PD1 <attacker> <damageX100> <attackType>               reliable, host -> joiner


// ------------------------------------------------------------
// ENUMS (values shared with proto.py)
// ------------------------------------------------------------

enum CP2077CoopNpcKind {
    Unknown = 0,
    CrowdNpc = 1,
    CombatNpc = 2,
    QuestNpc = 3,
    Vehicle = 4
}

enum CP2077CoopMoveState {
    Idle = 0,
    Walk = 1,
    Run = 2,
    Sprint = 3,
    CrouchIdle = 4,
    CrouchMove = 5,
    Vehicle = 10,
    Dead = 11
}

// EntityFlag bits
public func CP2077CoopNpcFlag_Dead() -> Int32 { return 1; }
public func CP2077CoopNpcFlag_Combat() -> Int32 { return 2; }
public func CP2077CoopNpcFlag_WeaponDrawn() -> Int32 { return 4; }
public func CP2077CoopNpcFlag_Crouched() -> Int32 { return 8; }
public func CP2077CoopNpcFlag_Ragdoll() -> Int32 { return 16; }
public func CP2077CoopNpcFlag_Hostile() -> Int32 { return 32; }
// bits above 0xFF are text-format extensions (not in the v2 u8 flags field)
public func CP2077CoopNpcFlag_Defeated() -> Int32 { return 256; }
public func CP2077CoopNpcFlag_Workspot() -> Int32 { return 512; }
public func CP2077CoopNpcFlag_Mounted() -> Int32 { return 1024; }
public func CP2077CoopNpcFlag_Teleported() -> Int32 { return 2048; }

// SpawnFlag bits
public func CP2077CoopSpawnFlag_Crowd() -> Int32 { return 1; }
public func CP2077CoopSpawnFlag_Traffic() -> Int32 { return 2; }
public func CP2077CoopSpawnFlag_Quest() -> Int32 { return 4; }
public func CP2077CoopSpawnFlag_Persistent() -> Int32 { return 8; }
// text-format extension: worldId is a static EntityID the joiner should bind to
public func CP2077CoopSpawnFlag_StaticId() -> Int32 { return 16; }

// target codes (anything else = netId of another synced entity)
public func CP2077CoopNpcTarget_None() -> Int32 { return 0; }
public func CP2077CoopNpcTarget_Joiner() -> Int32 { return 65534; }
public func CP2077CoopNpcTarget_Host() -> Int32 { return 65535; }

// DynamicEntitySystem tags
public func CP2077CoopNpc_AvatarTag() -> CName { return n"CP2077Coop.Remote"; }
public func CP2077CoopNpc_ProxyTag() -> CName { return n"CP2077Coop.NpcProxy"; }

// Largest payload that fits one CPN2 datagram (1200 - 20 byte header), with margin.
public func CP2077CoopNpc_MaxPayloadBytes() -> Int32 { return 1100; }


// ------------------------------------------------------------
// STATE
// ------------------------------------------------------------

public struct CP2077CoopNpcState {
    public let netId: Int32;
    public let entityID: EntityID;
    public let worldId: Uint64;          // 0 when the EntityID is dynamic
    public let kind: Int32;              // CP2077CoopNpcKind
    public let spawnFlags: Int32;
    public let attitude: Int32;          // EAIAttitude towards the host player (0 friendly, 1 neutral, 2 hostile)
    public let record: TweakDBID;
    public let appearance: CName;
    public let position: Vector4;
    public let yaw: Float;               // degrees
    public let velocity: Vector4;        // m/s
    public let moveState: Int32;         // CP2077CoopMoveState
    public let flags: Int32;
    public let health: Float;            // 0..1
    public let target: Int32;
    public let priority: Float;          // host only, lower = more important
}


// A fresh, zeroed state. Use it instead of a bare `let s: CP2077CoopNpcState;`
// inside loops: redscript locals keep the previous iteration's values.
public func CP2077CoopNpc_EmptyState() -> CP2077CoopNpcState {
    let state: CP2077CoopNpcState;
    return state;
}


// ------------------------------------------------------------
// QUANTIZATION
// ------------------------------------------------------------

public func CP2077CoopNpc_MetresToCm(value: Float) -> Int32 {
    return RoundF(value * 100.0);
}

public func CP2077CoopNpc_CmToMetres(value: Int32) -> Float {
    return Cast<Float>(value) / 100.0;
}

// yaw degrees -> 0..65535 (same as proto.yaw_to_u16)
public func CP2077CoopNpc_YawToU16(yaw: Float) -> Int32 {
    let turns: Float = yaw / 360.0;
    turns -= Cast<Float>(FloorF(turns));
    return RoundF(turns * 65536.0) % 65536;
}

public func CP2077CoopNpc_U16ToYaw(value: Int32) -> Float {
    let yaw: Float = Cast<Float>(value) * 360.0 / 65536.0;
    if yaw > 180.0 {
        yaw -= 360.0;
    }
    return yaw;
}

public func CP2077CoopNpc_HealthToByte(health: Float) -> Int32 {
    return Clamp(RoundF(health * 255.0), 0, 255);
}

public func CP2077CoopNpc_HasFlag(flags: Int32, flag: Int32) -> Bool {
    return (flags & flag) != 0;
}

// TweakDBID = name hash (low 32 bits) + name length (next 8 bits) + runtime offset (high 24 bits).
public func CP2077CoopNpc_RecordHash(record: TweakDBID) -> Uint32 {
    return Cast<Uint32>(TDBID.ToNumber(record));
}

public func CP2077CoopNpc_RecordLength(record: TweakDBID) -> Int32 {
    let shifted: Uint64 = BitShiftR64(TDBID.ToNumber(record), 32);
    return Cast<Int32>(shifted) & 255;
}

public func CP2077CoopNpc_IsStaticId(id: EntityID) -> Bool {
    return EntityID.IsDefined(id) && EntityID.IsStatic(id);
}

public func CP2077CoopNpc_WorldId(id: EntityID) -> Uint64 {
    if CP2077CoopNpc_IsStaticId(id) {
        return EntityID.ToHash(id);
    }
    return 0ul;
}


// ------------------------------------------------------------
// TEXT CODEC
// ------------------------------------------------------------

public func CP2077CoopNpc_EncodeEntry(state: CP2077CoopNpcState, anchor: Vector4) -> String {
    return IntToString(state.netId)
        + "," + IntToString(CP2077CoopNpc_MetresToCm(state.position.X - anchor.X))
        + "," + IntToString(CP2077CoopNpc_MetresToCm(state.position.Y - anchor.Y))
        + "," + IntToString(CP2077CoopNpc_MetresToCm(state.position.Z - anchor.Z))
        + "," + IntToString(CP2077CoopNpc_YawToU16(state.yaw))
        + "," + IntToString(CP2077CoopNpc_MetresToCm(state.velocity.X))
        + "," + IntToString(CP2077CoopNpc_MetresToCm(state.velocity.Y))
        + "," + IntToString(state.moveState)
        + "," + IntToString(state.flags)
        + "," + IntToString(CP2077CoopNpc_HealthToByte(state.health))
        + "," + IntToString(state.target);
}

// Splits the snapshot into as many NS1 messages as needed to keep each under the payload limit.
public func CP2077CoopNpc_EncodeSnapshot(states: array<CP2077CoopNpcState>, anchorIn: Vector4, seq: Int32, hostMs: Int32) -> array<String> {
    let anchor: Vector4 = new Vector4(Cast<Float>(RoundF(anchorIn.X)), Cast<Float>(RoundF(anchorIn.Y)), Cast<Float>(RoundF(anchorIn.Z)), 1.0);
    let chunks: array<String>;
    let current: String = "";
    let i: Int32 = 0;

    while i < ArraySize(states) {
        let entry: String = CP2077CoopNpc_EncodeEntry(states[i], anchor);
        // 64 bytes reserved for the header
        if StrLen(current) > 0 && StrLen(current) + StrLen(entry) + 65 > CP2077CoopNpc_MaxPayloadBytes() {
            ArrayPush(chunks, current);
            current = "";
        }
        current = StrLen(current) > 0 ? current + ";" + entry : entry;
        i += 1;
    }

    if StrLen(current) > 0 || ArraySize(chunks) == 0 {
        ArrayPush(chunks, current);
    }

    let header: String = IntToString(seq) + " " + IntToString(hostMs)
        + " " + IntToString(RoundF(anchor.X)) + " " + IntToString(RoundF(anchor.Y)) + " " + IntToString(RoundF(anchor.Z));
    let messages: array<String>;
    let part: Int32 = 0;

    while part < ArraySize(chunks) {
        ArrayPush(messages, "NS1 " + header + " " + IntToString(part) + " " + IntToString(ArraySize(chunks)) + " " + chunks[part]);
        part += 1;
    }

    return messages;
}

public func CP2077CoopNpc_EncodeBind(state: CP2077CoopNpcState) -> String {
    return "NB1 " + IntToString(state.netId)
        + " " + IntToString(state.kind)
        + " " + IntToString(state.spawnFlags)
        + " " + IntToString(state.attitude)
        + " " + ToString(CP2077CoopNpc_RecordHash(state.record))
        + " " + IntToString(CP2077CoopNpc_RecordLength(state.record))
        + " " + ToString(NameToHash(state.appearance))
        + " " + ToString(state.worldId)
        + " " + IntToString(CP2077CoopNpc_MetresToCm(state.position.X))
        + " " + IntToString(CP2077CoopNpc_MetresToCm(state.position.Y))
        + " " + IntToString(CP2077CoopNpc_MetresToCm(state.position.Z))
        + " " + IntToString(CP2077CoopNpc_YawToU16(state.yaw));
}

// Decodes one NS1 entry. Returns false for malformed input.
public func CP2077CoopNpc_DecodeEntry(text: String, anchor: Vector4, out state: CP2077CoopNpcState) -> Bool {
    let fields: array<String> = StrSplit(text, ",");

    if ArraySize(fields) != 11 {
        return false;
    }

    state.netId = StringToInt(fields[0], 0);
    state.position = new Vector4(
        anchor.X + CP2077CoopNpc_CmToMetres(StringToInt(fields[1], 0)),
        anchor.Y + CP2077CoopNpc_CmToMetres(StringToInt(fields[2], 0)),
        anchor.Z + CP2077CoopNpc_CmToMetres(StringToInt(fields[3], 0)),
        1.0
    );
    state.yaw = CP2077CoopNpc_U16ToYaw(StringToInt(fields[4], 0));
    state.velocity = new Vector4(CP2077CoopNpc_CmToMetres(StringToInt(fields[5], 0)), CP2077CoopNpc_CmToMetres(StringToInt(fields[6], 0)), 0.0, 0.0);
    state.moveState = StringToInt(fields[7], 0);
    state.flags = StringToInt(fields[8], 0);
    state.health = Cast<Float>(StringToInt(fields[9], 255)) / 255.0;
    state.target = StringToInt(fields[10], 0);

    return state.netId > 0;
}

// Decodes an NS1 message into states with absolute positions.
public func CP2077CoopNpc_DecodeSnapshot(message: String, out seq: Int32, out states: array<CP2077CoopNpcState>) -> Bool {
    let fields: array<String> = StrSplit(message, " ");

    if ArraySize(fields) < 8 || NotEquals(fields[0], "NS1") {
        return false;
    }

    seq = StringToInt(fields[1], 0);
    let anchor: Vector4 = new Vector4(
        Cast<Float>(StringToInt(fields[3], 0)),
        Cast<Float>(StringToInt(fields[4], 0)),
        Cast<Float>(StringToInt(fields[5], 0)),
        1.0
    );

    ArrayClear(states);

    if ArraySize(fields) < 9 || StrLen(fields[8]) == 0 {
        return true;
    }

    let entries: array<String> = StrSplit(fields[8], ";");
    let i: Int32 = 0;

    while i < ArraySize(entries) {
        let state: CP2077CoopNpcState = CP2077CoopNpc_EmptyState();
        if CP2077CoopNpc_DecodeEntry(entries[i], anchor, state) {
            ArrayPush(states, state);
        }
        i += 1;
    }

    return true;
}
