// CP2077Coop NPC sync - HOST side.
//
// Every entity tick (10 Hz) the host:
//   1. enumerates live NPCs around the host player AND around the joiner's
//      avatar (TargetingSystem query via GameObject.GetNPCsAroundObject),
//   2. captures quantizable state, ranks by distance/combat, keeps maxEntities,
//   3. gives new NPCs a netId and emits NB1 (bind) on the reliable channel,
//   4. emits ND1 once when a tracked NPC dies and NU1 when it leaves coverage,
//   5. emits NS1 snapshots (unreliable) and an NI1 coverage index every second.
//
// Only what the host's own game has streamed in can be synced: NPCs exist only
// within the host's streaming/spawn range (~100-150 m around the host player).


public class CP2077CoopNpcHostEntry extends IScriptable {
    public let netId: Int32;
    public let entityID: EntityID;
    public let entity: wref<GameObject>;
    public let kind: Int32;
    public let lastSeen: Float;
    public let deathSent: Bool;
    public let lastSentTime: Float;
    public let lastSentPosition: Vector4;
    public let lastSentFlags: Int32;
}

public class CP2077CoopNpcHost extends IScriptable {
    private let m_entries: array<ref<CP2077CoopNpcHostEntry>>;
    private let m_lastNetId: Int32;
    private let m_snapshotSeq: Int32;
    private let m_indexSeq: Int32;
    private let m_lastIndexTime: Float;

    public let radius: Float;
    public let maxEntities: Int32;
    public let vehicleRadius: Float;
    public let maxVehicles: Int32;
    // keep an NPC bound this long after it was last seen in range
    public let unbindDelay: Float;
    // idle, unchanged NPCs are resent at most this often (seconds)
    public let idleResendInterval: Float;

    public func Configure(radius: Float, maxEntities: Int32) -> Void {
        this.radius = radius;
        this.maxEntities = maxEntities;
        this.vehicleRadius = radius * 1.5;
        this.maxVehicles = 16;
        this.unbindDelay = 2.0;
        this.idleResendInterval = 0.5;
    }

    public func Reset() -> Void {
        ArrayClear(this.m_entries);
        this.m_lastNetId = 0;
        this.m_snapshotSeq = 0;
        this.m_indexSeq = 0;
        this.m_lastIndexTime = 0.0;
    }

    // ------------------------------------------------------------
    // ENUMERATION
    // ------------------------------------------------------------

    private func IsExcluded(npc: ref<NPCPuppet>) -> Bool {
        if !IsDefined(npc) || !npc.IsAttached() {
            return true;
        }

        // our own avatar / proxies / remote vehicle (and anything other mods spawn via DES)
        let dynamicEntities = GameInstance.GetDynamicEntitySystem();
        if IsDefined(dynamicEntities) && dynamicEntities.IsManaged(npc.GetEntityID()) {
            return true;
        }

        return false;
    }

    private func AppendUnique(out list: array<ref<NPCPuppet>>, candidates: array<ref<NPCPuppet>>) -> Void {
        let i: Int32 = 0;
        while i < ArraySize(candidates) {
            let candidate: ref<NPCPuppet> = candidates[i];
            if !this.IsExcluded(candidate) {
                let known: Bool = false;
                let j: Int32 = 0;
                while j < ArraySize(list) && !known {
                    known = list[j].GetEntityID() == candidate.GetEntityID();
                    j += 1;
                }
                if !known {
                    ArrayPush(list, candidate);
                }
            }
            i += 1;
        }
    }

    // NPCs around the host player and around the joiner's avatar (if spawned).
    public func FindNearbyNpcs(player: ref<PlayerPuppet>, avatar: ref<GameObject>) -> array<ref<NPCPuppet>> {
        let result: array<ref<NPCPuppet>>;
        this.AppendUnique(result, player.GetNPCsAroundObject(this.radius));
        if IsDefined(avatar) {
            this.AppendUnique(result, avatar.GetNPCsAroundObject(this.radius));
        }
        return result;
    }

    // ------------------------------------------------------------
    // STATE CAPTURE
    // ------------------------------------------------------------

    public static func TargetCode(target: ref<GameObject>, player: ref<PlayerPuppet>, avatar: ref<GameObject>, host: ref<CP2077CoopNpcHost>) -> Int32 {
        if !IsDefined(target) {
            return CP2077CoopNpcTarget_None();
        }
        if target == player {
            return CP2077CoopNpcTarget_Host();
        }
        if IsDefined(avatar) && target == avatar {
            return CP2077CoopNpcTarget_Joiner();
        }
        if IsDefined(host) {
            let entry = host.FindByEntityID(target.GetEntityID());
            if IsDefined(entry) {
                return entry.netId;
            }
        }
        return CP2077CoopNpcTarget_None();
    }

    public static func MoveStateFor(speed: Float, crouched: Bool, dead: Bool, mounted: Bool) -> Int32 {
        if dead {
            return EnumInt(CP2077CoopMoveState.Dead);
        }
        if mounted {
            return EnumInt(CP2077CoopMoveState.Vehicle);
        }
        if crouched {
            return speed > 0.3 ? EnumInt(CP2077CoopMoveState.CrouchMove) : EnumInt(CP2077CoopMoveState.CrouchIdle);
        }
        if speed < 0.3 {
            return EnumInt(CP2077CoopMoveState.Idle);
        }
        if speed < 2.2 {
            return EnumInt(CP2077CoopMoveState.Walk);
        }
        if speed < 4.5 {
            return EnumInt(CP2077CoopMoveState.Run);
        }
        return EnumInt(CP2077CoopMoveState.Sprint);
    }

    // Kind is decided once, at bind time, so later combat does not "respawn" the proxy.
    public static func KindFor(npc: ref<NPCPuppet>) -> Int32 {
        if CP2077CoopNpc_IsStaticId(npc.GetEntityID()) {
            return EnumInt(CP2077CoopNpcKind.QuestNpc);
        }
        if npc.IsCrowd() || npc.IsCharacterCivilian() {
            return EnumInt(CP2077CoopNpcKind.CrowdNpc);
        }
        return EnumInt(CP2077CoopNpcKind.CombatNpc);
    }

    public func Capture(npc: ref<NPCPuppet>, player: ref<PlayerPuppet>, avatar: ref<GameObject>) -> CP2077CoopNpcState {
        let state: CP2077CoopNpcState = CP2077CoopNpc_EmptyState();
        let game: GameInstance = npc.GetGame();
        let id: EntityID = npc.GetEntityID();

        state.entityID = id;
        state.worldId = CP2077CoopNpc_WorldId(id);
        state.kind = CP2077CoopNpcHost.KindFor(npc);
        state.record = npc.GetRecordID();
        state.appearance = npc.GetCurrentAppearanceName();
        state.position = npc.GetWorldPosition();
        state.yaw = npc.GetWorldYaw();
        state.velocity = npc.GetVelocity();
        state.attitude = EnumInt(GameObject.GetAttitudeTowards(npc, player));

        if npc.IsCrowd() {
            state.spawnFlags = state.spawnFlags | CP2077CoopSpawnFlag_Crowd();
        }
        if state.worldId != 0ul {
            state.spawnFlags = state.spawnFlags | CP2077CoopSpawnFlag_StaticId() | CP2077CoopSpawnFlag_Persistent();
        }

        let dead: Bool = npc.IsDead();
        let highLevel: gamedataNPCHighLevelState = npc.GetHighLevelStateFromBlackboard();
        let stance: gamedataNPCStanceState = npc.GetStanceStateFromBlackboard();
        let mounted: Bool = VehicleComponent.IsMountedToVehicle(game, npc);
        let crouched: Bool = Equals(stance, gamedataNPCStanceState.Crouch) || Equals(stance, gamedataNPCStanceState.Cover);
        let speed: Float = SqrtF(state.velocity.X * state.velocity.X + state.velocity.Y * state.velocity.Y);

        let flags: Int32 = 0;
        if dead {
            flags = flags | CP2077CoopNpcFlag_Dead();
        }
        if Equals(highLevel, gamedataNPCHighLevelState.Combat) {
            flags = flags | CP2077CoopNpcFlag_Combat();
        }
        if IsDefined(ScriptedPuppet.GetActiveWeapon(npc)) {
            flags = flags | CP2077CoopNpcFlag_WeaponDrawn();
        }
        if crouched {
            flags = flags | CP2077CoopNpcFlag_Crouched();
        }
        if IsDefined(avatar) && Equals(GameObject.GetAttitudeTowards(npc, avatar), EAIAttitude.AIA_Hostile) {
            flags = flags | CP2077CoopNpcFlag_Hostile();
        }
        if ScriptedPuppet.IsDefeated(npc) {
            flags = flags | CP2077CoopNpcFlag_Defeated();
        }
        if GameInstance.GetWorkspotSystem(game).IsActorInWorkspot(npc) {
            flags = flags | CP2077CoopNpcFlag_Workspot();
        }
        if mounted {
            flags = flags | CP2077CoopNpcFlag_Mounted();
        }
        state.flags = flags;
        state.moveState = CP2077CoopNpcHost.MoveStateFor(speed, crouched, dead, mounted);
        state.health = GameInstance.GetStatPoolsSystem(game).GetStatPoolValue(Cast<StatsObjectID>(id), gamedataStatPoolType.Health, true) / 100.0;

        let ai: ref<AIHumanComponent> = npc.GetAIControllerComponent();
        if IsDefined(ai) {
            let combatTarget: wref<GameObject> = FromVariant<wref<GameObject>>(ai.GetBehaviorArgument(n"CombatTarget"));
            state.target = CP2077CoopNpcHost.TargetCode(combatTarget, player, avatar, this);
        }

        // ranking: closest to either player first, fights first
        let distance: Float = Vector4.Distance(state.position, player.GetWorldPosition());
        if IsDefined(avatar) {
            distance = MinF(distance, Vector4.Distance(state.position, avatar.GetWorldPosition()));
        }
        state.priority = CP2077CoopNpc_HasFlag(flags, CP2077CoopNpcFlag_Combat()) ? distance - 40.0 : distance;

        return state;
    }

    // Live NPCs near both players, best first, at most maxEntities.
    public func Collect(player: ref<PlayerPuppet>, avatar: ref<GameObject>) -> array<CP2077CoopNpcState> {
        let npcs: array<ref<NPCPuppet>> = this.FindNearbyNpcs(player, avatar);
        let states: array<CP2077CoopNpcState>;
        let i: Int32 = 0;

        while i < ArraySize(npcs) {
            let state: CP2077CoopNpcState = this.Capture(npcs[i], player, avatar);
            // traffic drivers/passengers: the vehicle proxy stands in for them (phase 1)
            let trafficOccupant: Bool = CP2077CoopNpc_HasFlag(state.flags, CP2077CoopNpcFlag_Mounted()) && state.worldId == 0ul;
            if !trafficOccupant {
                ArrayPush(states, state);
            }
            i += 1;
        }

        // insertion sort by priority (n <= ~100)
        i = 1;
        while i < ArraySize(states) {
            let current: CP2077CoopNpcState = states[i];
            let j: Int32 = i - 1;
            while j >= 0 && states[j].priority > current.priority {
                states[j + 1] = states[j];
                j -= 1;
            }
            states[j + 1] = current;
            i += 1;
        }

        while ArraySize(states) > this.maxEntities {
            ArrayPop(states);
        }

        return states;
    }

    // ------------------------------------------------------------
    // TRAFFIC / VEHICLES
    // ------------------------------------------------------------

    // Vehicles come from the Codeware Entity/Attach registry (the targeting query
    // only returns puppets). Synced: traffic and runtime-spawned vehicles, and
    // static (parked) vehicles once they move or were bound before. Excluded: the
    // host's own car (player sync carries it) and anything spawned through DES.
    public func CollectVehicles(vehicles: array<wref<VehicleObject>>, player: ref<PlayerPuppet>, avatar: ref<GameObject>) -> array<CP2077CoopNpcState> {
        let states: array<CP2077CoopNpcState>;
        let game: GameInstance = player.GetGame();
        let dynamicEntities = GameInstance.GetDynamicEntitySystem();
        let mounted: wref<VehicleObject>;
        VehicleComponent.GetVehicle(game, player, mounted);
        let i: Int32 = 0;

        while i < ArraySize(vehicles) {
            let vehicle: ref<VehicleObject> = vehicles[i];
            if IsDefined(vehicle) && vehicle.IsAttached() && vehicle != mounted
                && !(IsDefined(dynamicEntities) && dynamicEntities.IsManaged(vehicle.GetEntityID())) {
                let state: CP2077CoopNpcState = this.CaptureVehicle(vehicle, player, avatar);
                let bound: Bool = IsDefined(this.FindByEntityID(state.entityID));
                let moving: Bool = Vector4.Length(state.velocity) > 0.5;
                let inRange: Bool = state.priority <= this.vehicleRadius;
                if inRange && (state.worldId == 0ul || moving || bound) {
                    ArrayPush(states, state);
                }
            }
            i += 1;
        }

        i = 1;
        while i < ArraySize(states) {
            let current: CP2077CoopNpcState = states[i];
            let j: Int32 = i - 1;
            while j >= 0 && states[j].priority > current.priority {
                states[j + 1] = states[j];
                j -= 1;
            }
            states[j + 1] = current;
            i += 1;
        }
        while ArraySize(states) > this.maxVehicles {
            ArrayPop(states);
        }
        return states;
    }

    public func CaptureVehicle(vehicle: ref<VehicleObject>, player: ref<PlayerPuppet>, avatar: ref<GameObject>) -> CP2077CoopNpcState {
        let state: CP2077CoopNpcState = CP2077CoopNpc_EmptyState();
        let id: EntityID = vehicle.GetEntityID();

        state.entityID = id;
        state.worldId = CP2077CoopNpc_WorldId(id);
        state.kind = EnumInt(CP2077CoopNpcKind.Vehicle);
        state.spawnFlags = vehicle.IsCrowdVehicle() ? CP2077CoopSpawnFlag_Traffic() : 0;
        if state.worldId != 0ul {
            state.spawnFlags = state.spawnFlags | CP2077CoopSpawnFlag_StaticId() | CP2077CoopSpawnFlag_Persistent();
        }
        state.record = vehicle.GetRecordID();
        state.appearance = vehicle.GetCurrentAppearanceName();
        state.position = vehicle.GetWorldPosition();
        state.yaw = vehicle.GetWorldYaw();
        state.velocity = vehicle.GetLinearVelocity();
        state.moveState = EnumInt(CP2077CoopMoveState.Vehicle);
        state.attitude = EnumInt(EAIAttitude.AIA_Neutral);
        state.health = GameInstance.GetStatPoolsSystem(vehicle.GetGame()).GetStatPoolValue(Cast<StatsObjectID>(id), gamedataStatPoolType.Health, true) / 100.0;

        let distance: Float = Vector4.Distance(state.position, player.GetWorldPosition());
        if IsDefined(avatar) {
            distance = MinF(distance, Vector4.Distance(state.position, avatar.GetWorldPosition()));
        }
        state.priority = distance;
        return state;
    }

    // ------------------------------------------------------------
    // NET TABLE
    // ------------------------------------------------------------

    public func FindByEntityID(id: EntityID) -> ref<CP2077CoopNpcHostEntry> {
        let i: Int32 = 0;
        while i < ArraySize(this.m_entries) {
            if this.m_entries[i].entityID == id {
                return this.m_entries[i];
            }
            i += 1;
        }
        return null;
    }

    public func FindByNetId(netId: Int32) -> ref<CP2077CoopNpcHostEntry> {
        let i: Int32 = 0;
        while i < ArraySize(this.m_entries) {
            if this.m_entries[i].netId == netId {
                return this.m_entries[i];
            }
            i += 1;
        }
        return null;
    }

    private func AllocateNetId() -> Int32 {
        let attempts: Int32 = 0;
        while attempts < 65000 {
            this.m_lastNetId += 1;
            if this.m_lastNetId >= CP2077CoopNpcTarget_Joiner() {
                this.m_lastNetId = 1;
            }
            if !IsDefined(this.FindByNetId(this.m_lastNetId)) {
                return this.m_lastNetId;
            }
            attempts += 1;
        }
        return 0;
    }

    // Should this state go into this tick's snapshot? Moving / fighting NPCs: every tick.
    // Idle, unchanged NPCs: every idleResendInterval (still sent so loss heals itself).
    private func NeedsSend(entry: ref<CP2077CoopNpcHostEntry>, state: CP2077CoopNpcState, now: Float) -> Bool {
        if state.moveState != EnumInt(CP2077CoopMoveState.Idle) && state.moveState != EnumInt(CP2077CoopMoveState.CrouchIdle) {
            return true;
        }
        if state.flags != entry.lastSentFlags {
            return true;
        }
        if Vector4.Distance(state.position, entry.lastSentPosition) > 0.05 {
            return true;
        }
        return now - entry.lastSentTime >= this.idleResendInterval;
    }

    // One entity tick. Appends reliable messages (bind/death/unbind) and
    // unreliable ones (snapshot parts, coverage index) to the given arrays.
    public func Tick(player: ref<PlayerPuppet>, avatar: ref<GameObject>, vehicles: array<wref<VehicleObject>>, now: Float, out reliable: array<String>, out unreliable: array<String>) -> Void {
        let states: array<CP2077CoopNpcState> = this.Collect(player, avatar);
        let vehicleStates: array<CP2077CoopNpcState> = this.CollectVehicles(vehicles, player, avatar);
        let v: Int32 = 0;
        while v < ArraySize(vehicleStates) {
            ArrayPush(states, vehicleStates[v]);
            v += 1;
        }
        let toSend: array<CP2077CoopNpcState>;
        let i: Int32 = 0;

        while i < ArraySize(states) {
            let state: CP2077CoopNpcState = states[i];
            let entry: ref<CP2077CoopNpcHostEntry> = this.FindByEntityID(state.entityID);

            if !IsDefined(entry) {
                let netId: Int32 = this.AllocateNetId();
                if netId > 0 {
                    entry = new CP2077CoopNpcHostEntry();
                    entry.netId = netId;
                    entry.entityID = state.entityID;
                    entry.kind = state.kind;
                    entry.lastSentTime = -100.0;
                    ArrayPush(this.m_entries, entry);
                    state.netId = netId;
                    ArrayPush(reliable, CP2077CoopNpc_EncodeBind(state));
                }
            }

            if IsDefined(entry) {
                entry.entity = GameInstance.FindEntityByID(player.GetGame(), state.entityID) as GameObject;
                entry.lastSeen = now;
                state.netId = entry.netId;
                state.kind = entry.kind;
                if this.NeedsSend(entry, state, now) {
                    entry.lastSentTime = now;
                    entry.lastSentPosition = state.position;
                    entry.lastSentFlags = state.flags;
                    ArrayPush(toSend, state);
                }
            }
            i += 1;
        }

        this.UpdateLifecycle(now, reliable);

        this.m_snapshotSeq = (this.m_snapshotSeq + 1) % 65536;
        let hostMs: Int32 = RoundF(now * 1000.0) % 2000000000;
        let parts: array<String> = CP2077CoopNpc_EncodeSnapshot(toSend, player.GetWorldPosition(), this.m_snapshotSeq, hostMs);
        i = 0;
        while i < ArraySize(parts) {
            ArrayPush(unreliable, parts[i]);
            i += 1;
        }

        if now - this.m_lastIndexTime >= 1.0 {
            this.m_lastIndexTime = now;
            ArrayPush(unreliable, this.EncodeIndex(player.GetWorldPosition()));
        }
    }

    // Deaths (once) and unbinds (despawned, or out of coverage for unbindDelay).
    private func UpdateLifecycle(now: Float, out reliable: array<String>) -> Void {
        let i: Int32 = ArraySize(this.m_entries) - 1;

        while i >= 0 {
            let entry: ref<CP2077CoopNpcHostEntry> = this.m_entries[i];
            let object: ref<GameObject> = entry.entity;
            let npc: ref<NPCPuppet> = object as NPCPuppet;
            let remove: Bool = false;
            let reason: Int32 = 0;

            if !IsDefined(object) || !object.IsAttached() {
                remove = true;
                reason = 1;
            } else {
                if !entry.deathSent && IsDefined(npc) && npc.IsDead() {
                    entry.deathSent = true;
                    ArrayPush(reliable, "ND1 " + IntToString(entry.netId) + " 0 1");
                }
                // dead NPCs leave the alive-only targeting query; keep the corpse bound a while longer
                let timeout: Float = entry.deathSent ? this.unbindDelay * 15.0 : this.unbindDelay;
                if now - entry.lastSeen > timeout {
                    remove = true;
                    reason = 0;
                }
            }

            if remove {
                ArrayPush(reliable, "NU1 " + IntToString(entry.netId) + " " + IntToString(reason));
                ArrayErase(this.m_entries, i);
            }
            i -= 1;
        }
    }

    private func EncodeIndex(center: Vector4) -> String {
        let ids: String = "";
        let i: Int32 = 0;
        while i < ArraySize(this.m_entries) {
            ids = i == 0 ? IntToString(this.m_entries[i].netId) : ids + ";" + IntToString(this.m_entries[i].netId);
            i += 1;
        }
        this.m_indexSeq = (this.m_indexSeq + 1) % 65536;
        return "NI1 " + IntToString(this.m_indexSeq)
            + " " + IntToString(RoundF(center.X)) + " " + IntToString(RoundF(center.Y)) + " " + IntToString(RoundF(center.Z))
            + " " + IntToString(RoundF(this.radius)) + " " + ids;
    }

    // ------------------------------------------------------------
    // HITS FROM THE JOINER (NH1)
    // ------------------------------------------------------------

    // Applies damage the joiner dealt to a synced NPC. The joiner's avatar is the
    // instigator, so the NPC turns on the avatar (= the joiner) rather than the host.
    public func ApplyJoinerHit(message: String, avatar: ref<GameObject>) -> Bool {
        let fields: array<String> = StrSplit(message, " ");
        if ArraySize(fields) < 5 || NotEquals(fields[0], "NH1") {
            return false;
        }

        let entry = this.FindByNetId(StringToInt(fields[1], 0));
        if !IsDefined(entry) {
            return false;
        }

        let npc: ref<NPCPuppet> = entry.entity as NPCPuppet;
        let damage: Float = ClampF(Cast<Float>(StringToInt(fields[2], 0)) / 100.0, 0.0, 100000.0);
        if !IsDefined(npc) || npc.IsDead() || damage <= 0.0 {
            return false;
        }

        let game: GameInstance = npc.GetGame();
        if GameInstance.GetGodModeSystem(game).HasGodMode(npc.GetEntityID(), gameGodModeType.Invulnerable) {
            return false;
        }

        GameInstance.GetStatPoolsSystem(game).RequestChangingStatPoolValue(
            Cast<StatsObjectID>(npc.GetEntityID()), gamedataStatPoolType.Health, -damage, avatar, false, false);

        let avatarPuppet: ref<ScriptedPuppet> = avatar as ScriptedPuppet;
        if IsDefined(avatarPuppet) {
            TargetTrackingExtension.InjectThreat(npc, avatarPuppet);
        }
        return true;
    }
}
