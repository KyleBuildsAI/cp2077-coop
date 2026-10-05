// CP2077Coop NPC sync - JOINER side.
//
//   * Suppresses the joiner's own random population (crowd density modifier 0,
//     a dynamic crowd null area around the joiner, police off) and hides any
//     dynamic NPC / crowd vehicle the joiner's game still spawns.
//   * NB1 binds a host netId to either the joiner's own copy of a static NPC
//     ("mirror", same EntityID in both games) or to a proxy spawned through the
//     DynamicEntitySystem from record + appearance.
//   * NS1 drives mirrors and proxies towards the host state: AIMoveToCommand for
//     walking (keeps locomotion animation), AITeleportCommand for large errors,
//     AIRotateToCommand when idle. Their own senses are switched off so they do
//     not react to the joiner on their own; the host decides fights and deaths.
//   * ND1 kills the local copy (death animation / ragdoll); NU1 releases it.


public class CP2077CoopNpcProxy extends IScriptable {
    public let netId: Int32;
    public let kind: Int32;
    public let spawnFlags: Int32;
    public let worldId: Uint64;
    public let record: TweakDBID;
    public let appearance: CName;
    public let bindPosition: Vector4;
    public let bindYaw: Float;
    public let bindTime: Float;

    public let localId: EntityID;
    public let isMirror: Bool;
    public let spawnRequested: Bool;
    public let prepared: Bool;

    public let dead: Bool;
    public let hostile: Bool;
    public let crouched: Bool;
    public let weaponDrawn: Bool;

    public let hasState: Bool;
    public let lastState: CP2077CoopNpcState;
    public let lastMoveTarget: Vector4;
    public let lastMoveTime: Float;
    public let lastRotateTime: Float;
    public let moveCommand: ref<AIMoveToCommand>;
    public let lastReceiveTime: Float;
}

public class CP2077CoopNpcJoiner extends IScriptable {
    private let m_proxies: array<ref<CP2077CoopNpcProxy>>;
    private let m_hidden: array<wref<GameObject>>;
    private let m_lastSeq: Int32;
    private let m_hasSeq: Bool;

    private let m_suppressed: Bool;
    private let m_nullAreaId: Uint64;
    private let m_nullAreaCenter: Vector4;
    private let m_nullAreaTime: Float;

    private let m_coverageCount: Int32;
    private let m_coverageRadius: Float;
    private let m_coverageCenter: Vector4;
    private let m_hasCoverage: Bool;
    private let m_lastSweep: Float;
    // true while the joiner is too far from the host for host-side population
    private let m_detached: Bool;

    public let maxEntities: Int32;
    public let teleportDistance: Float;
    public let moveLead: Float;
    public let mirrorWait: Float;
    public let nullAreaHalfSize: Float;
    // the host only has NPCs inside its own streaming range; beyond this distance
    // between the players the joiner falls back to its own local population
    public let leashDistance: Float;

    public func Configure(maxEntities: Int32) -> Void {
        this.maxEntities = maxEntities;
        this.teleportDistance = 4.0;
        this.moveLead = 0.3;
        this.mirrorWait = 2.0;
        this.nullAreaHalfSize = 150.0;
        this.leashDistance = 120.0;
    }

    // ------------------------------------------------------------
    // LOOKUP
    // ------------------------------------------------------------

    public func FindByNetId(netId: Int32) -> ref<CP2077CoopNpcProxy> {
        let i: Int32 = 0;
        while i < ArraySize(this.m_proxies) {
            if this.m_proxies[i].netId == netId {
                return this.m_proxies[i];
            }
            i += 1;
        }
        return null;
    }

    public func FindByLocalId(id: EntityID) -> ref<CP2077CoopNpcProxy> {
        if !EntityID.IsDefined(id) {
            return null;
        }
        let i: Int32 = 0;
        while i < ArraySize(this.m_proxies) {
            if this.m_proxies[i].localId == id {
                return this.m_proxies[i];
            }
            i += 1;
        }
        return null;
    }

    public func GetLocalObject(proxy: ref<CP2077CoopNpcProxy>) -> ref<GameObject> {
        if !IsDefined(proxy) || !EntityID.IsDefined(proxy.localId) {
            return null;
        }
        if proxy.isMirror {
            return GameInstance.FindEntityByID(GetGameInstance(), proxy.localId) as GameObject;
        }
        let dynamicEntities = GameInstance.GetDynamicEntitySystem();
        if !IsDefined(dynamicEntities) || !dynamicEntities.IsSpawned(proxy.localId) {
            return null;
        }
        return dynamicEntities.GetEntity(proxy.localId) as GameObject;
    }

    public func GetLocal(proxy: ref<CP2077CoopNpcProxy>) -> ref<NPCPuppet> {
        return this.GetLocalObject(proxy) as NPCPuppet;
    }

    public static func IsVehicleKind(proxy: ref<CP2077CoopNpcProxy>) -> Bool {
        return proxy.kind == EnumInt(CP2077CoopNpcKind.Vehicle);
    }

    // ------------------------------------------------------------
    // POPULATION SUPPRESSION
    // ------------------------------------------------------------

    public func IsSuppressing() -> Bool {
        return this.m_suppressed;
    }

    public func SetPopulationSuppressed(player: ref<PlayerPuppet>, enabled: Bool) -> Void {
        this.ApplyPopulationControl(player, enabled);
        this.m_suppressed = enabled;
        this.m_detached = false;
    }

    public func IsDetached() -> Bool {
        return this.m_detached;
    }

    private func ApplyPopulationControl(player: ref<PlayerPuppet>, enabled: Bool) -> Void {
        let game: GameInstance = player.GetGame();
        let community: ref<CommunitySystem> = GameInstance.GetCommunitySystem(game);

        if enabled {
            if IsDefined(community) {
                community.ChangeDensityModifier(0.0);
            }
            this.RefreshNullArea(player, true);
        } else {
            if IsDefined(community) {
                if this.m_nullAreaId != 0ul {
                    community.DisableCrowdNullArea(this.m_nullAreaId);
                    this.m_nullAreaId = 0ul;
                }
                community.ResetDensityModifier();
            }
            this.UnhideAll();
        }

        // Police (PreventionSystem) is host-only: the joiner sees the host's units as proxies.
        let prevention = GameInstance.GetScriptableSystemsContainer(game).Get(n"PreventionSystem") as PreventionSystem;
        if IsDefined(prevention) {
            let toggle: ref<TogglePreventionSystem> = new TogglePreventionSystem();
            toggle.sourceName = n"CP2077Coop";
            toggle.isActive = !enabled;
            prevention.QueueRequest(toggle);
        }
    }

    // Leash: when the players drift apart beyond leashDistance the host cannot
    // provide NPCs around the joiner, so the joiner gets its own population back
    // until they are close again (30 m hysteresis).
    private func UpdateLeash(player: ref<PlayerPuppet>) -> Void {
        if !this.m_suppressed || !this.m_hasCoverage {
            return;
        }
        let distance: Float = Vector4.Distance(player.GetWorldPosition(), this.m_coverageCenter);
        if !this.m_detached && distance > this.leashDistance {
            this.m_detached = true;
            this.ApplyPopulationControl(player, false);
        } else {
            if this.m_detached && distance < this.leashDistance - 30.0 {
                this.m_detached = false;
                this.ApplyPopulationControl(player, true);
            }
        }
    }

    // Keeps a crowd null area (no new crowd spawns) centred on the joiner.
    public func RefreshNullArea(player: ref<PlayerPuppet>, force: Bool) -> Void {
        let position: Vector4 = player.GetWorldPosition();
        let age: Float = EngineTime.ToFloat(GameInstance.GetSimTime(player.GetGame())) - this.m_nullAreaTime;
        if !force && this.m_nullAreaId != 0ul && age < 30.0
            && Vector4.Distance(position, this.m_nullAreaCenter) < this.nullAreaHalfSize * 0.5 {
            return;
        }

        let community: ref<CommunitySystem> = GameInstance.GetCommunitySystem(player.GetGame());
        if !IsDefined(community) {
            return;
        }
        if this.m_nullAreaId != 0ul {
            community.DisableCrowdNullArea(this.m_nullAreaId);
        }

        let box: Box;
        box.Min = new Vector4(-this.nullAreaHalfSize, -this.nullAreaHalfSize, -60.0, 1.0);
        box.Max = new Vector4(this.nullAreaHalfSize, this.nullAreaHalfSize, 60.0, 1.0);
        let transform: WorldTransform;
        WorldTransform.SetPosition(transform, position);

        // finite lifetime, renewed by the sweep before it runs out: if the game is
        // left in a bad state the area still expires on its own
        this.m_nullAreaId = community.EnableDynamicCrowdNullArea(box, transform, false, 60.0);
        this.m_nullAreaCenter = position;
        this.m_nullAreaTime = EngineTime.ToFloat(GameInstance.GetSimTime(player.GetGame()));
    }

    // Hides an entity without destroying it: visual components off, collision off.
    public static func SetHidden(entity: ref<GameObject>, hidden: Bool) -> Void {
        if !IsDefined(entity) {
            return;
        }
        let components: array<ref<IComponent>> = entity.GetComponents();
        let i: Int32 = 0;
        while i < ArraySize(components) {
            if IsDefined(components[i]) && components[i].IsA(n"entIVisualComponent") {
                components[i].Toggle(!hidden);
            }
            i += 1;
        }

        let puppet: ref<ScriptedPuppet> = entity as ScriptedPuppet;
        if IsDefined(puppet) {
            let ai: ref<AIHumanComponent> = puppet.GetAIControllerComponent();
            if IsDefined(ai) {
                if hidden {
                    ai.DisableCollider();
                } else {
                    ai.EnableCollider();
                }
            }
            let senses: ref<SenseComponent> = puppet.GetSensesComponent();
            if IsDefined(senses) {
                senses.ToggleComponent(!hidden);
            }
        }
    }

    public func Hide(entity: ref<GameObject>) -> Void {
        if !IsDefined(entity) {
            return;
        }
        let i: Int32 = 0;
        while i < ArraySize(this.m_hidden) {
            let hidden: ref<GameObject> = this.m_hidden[i];
            if IsDefined(hidden) && hidden.GetEntityID() == entity.GetEntityID() {
                return;
            }
            i += 1;
        }
        CP2077CoopNpcJoiner.SetHidden(entity, true);
        ArrayPush(this.m_hidden, entity);
    }

    public func Unhide(entity: ref<GameObject>) -> Void {
        let i: Int32 = ArraySize(this.m_hidden) - 1;
        while i >= 0 {
            let hidden: ref<GameObject> = this.m_hidden[i];
            if !IsDefined(hidden) {
                ArrayErase(this.m_hidden, i);
            } else {
                if hidden.GetEntityID() == entity.GetEntityID() {
                    CP2077CoopNpcJoiner.SetHidden(hidden, false);
                    ArrayErase(this.m_hidden, i);
                }
            }
            i -= 1;
        }
    }

    private func UnhideAll() -> Void {
        let i: Int32 = 0;
        while i < ArraySize(this.m_hidden) {
            let hidden: ref<GameObject> = this.m_hidden[i];
            if IsDefined(hidden) {
                CP2077CoopNpcJoiner.SetHidden(hidden, false);
            }
            i += 1;
        }
        ArrayClear(this.m_hidden);
    }

    // Should this local entity be hidden because the host owns the population?
    public func ShouldSuppress(entity: ref<GameObject>) -> Bool {
        if !this.m_suppressed || this.m_detached || !IsDefined(entity) || entity.IsPlayer() {
            return false;
        }
        let id: EntityID = entity.GetEntityID();
        if IsDefined(this.FindByLocalId(id)) {
            return false;
        }
        let dynamicEntities = GameInstance.GetDynamicEntitySystem();
        if IsDefined(dynamicEntities) && dynamicEntities.IsManaged(id) {
            return false;
        }
        let vehicle: ref<VehicleObject> = entity as VehicleObject;
        if IsDefined(vehicle) {
            return vehicle.IsCrowdVehicle();
        }
        let npc: ref<NPCPuppet> = entity as NPCPuppet;
        if IsDefined(npc) {
            return npc.IsCrowd() || EntityID.IsDynamic(id);
        }
        return false;
    }

    // Once a second: hide local NPCs the host does not have.
    //  * dynamic / crowd NPCs: always (the host's population replaces them)
    //  * static NPCs near the joiner that the host did not bind although its
    //    last coverage index was not saturated (host would have included them)
    public func SweepLocalPopulation(player: ref<PlayerPuppet>, now: Float) -> Void {
        if !this.m_suppressed || now - this.m_lastSweep < 1.0 {
            return;
        }
        this.m_lastSweep = now;
        this.UpdateLeash(player);
        if this.m_detached {
            return;
        }
        this.RefreshNullArea(player, false);

        let radius: Float = this.m_coverageRadius > 0.0 ? this.m_coverageRadius : 60.0;
        let npcs: array<ref<NPCPuppet>> = player.GetNPCsAroundObject(radius);
        let saturated: Bool = this.m_coverageCount >= this.maxEntities;
        let i: Int32 = 0;

        while i < ArraySize(npcs) {
            let npc: ref<NPCPuppet> = npcs[i];
            if IsDefined(npc) && !IsDefined(this.FindByLocalId(npc.GetEntityID())) {
                if this.ShouldSuppress(npc) {
                    this.Hide(npc);
                } else {
                    let inner: Bool = Vector4.Distance(npc.GetWorldPosition(), player.GetWorldPosition()) < radius - 10.0;
                    if !saturated && inner && CP2077CoopNpc_IsStaticId(npc.GetEntityID()) && this.m_coverageCount > 0 {
                        this.Hide(npc);
                    }
                }
            }
            i += 1;
        }
    }

    public func OnCoverageIndex(message: String) -> Void {
        let fields: array<String> = StrSplit(message, " ");
        if ArraySize(fields) < 6 || NotEquals(fields[0], "NI1") {
            return;
        }
        this.m_coverageCenter = new Vector4(
            Cast<Float>(StringToInt(fields[2], 0)),
            Cast<Float>(StringToInt(fields[3], 0)),
            Cast<Float>(StringToInt(fields[4], 0)),
            1.0
        );
        this.m_hasCoverage = true;
        this.m_coverageRadius = Cast<Float>(StringToInt(fields[5], 60));
        this.m_coverageCount = ArraySize(fields) > 6 && StrLen(fields[6]) > 0 ? ArraySize(StrSplit(fields[6], ";")) : 0;
    }

    // ------------------------------------------------------------
    // BIND / UNBIND / DEATH
    // ------------------------------------------------------------

    // NB1 <netId> <kind> <spawnFlags> <attitude> <recHash> <recLen> <appearance> <worldId> <xCm> <yCm> <zCm> <yaw16>
    // `record` is rebuilt by the caller from recHash/recLen (CET: TweakDBID.new(hash, length)),
    // because redscript cannot construct a TweakDBID from a number.
    public func OnBind(message: String, record: TweakDBID, now: Float) -> Bool {
        let fields: array<String> = StrSplit(message, " ");
        if ArraySize(fields) != 13 || NotEquals(fields[0], "NB1") {
            return false;
        }

        let netId: Int32 = StringToInt(fields[1], 0);
        if netId <= 0 {
            return false;
        }

        let proxy: ref<CP2077CoopNpcProxy> = this.FindByNetId(netId);
        if IsDefined(proxy) {
            // re-bind (host restarted its table or a duplicate): drop the old binding first
            this.Release(proxy, true);
        }

        proxy = new CP2077CoopNpcProxy();
        proxy.netId = netId;
        proxy.kind = StringToInt(fields[2], 0);
        proxy.spawnFlags = StringToInt(fields[3], 0);
        proxy.hostile = StringToInt(fields[4], 1) == EnumInt(EAIAttitude.AIA_Hostile);
        proxy.record = record;
        proxy.appearance = HashToName(StringToUint64(fields[7], 0ul));
        proxy.worldId = StringToUint64(fields[8], 0ul);
        proxy.bindPosition = new Vector4(
            CP2077CoopNpc_CmToMetres(StringToInt(fields[9], 0)),
            CP2077CoopNpc_CmToMetres(StringToInt(fields[10], 0)),
            CP2077CoopNpc_CmToMetres(StringToInt(fields[11], 0)),
            1.0
        );
        proxy.bindYaw = CP2077CoopNpc_U16ToYaw(StringToInt(fields[12], 0));
        proxy.bindTime = now;
        ArrayPush(this.m_proxies, proxy);

        this.Resolve(proxy, now);
        return true;
    }

    // Mirror the joiner's own copy if the static EntityID exists here, otherwise
    // spawn a proxy (immediately for dynamic NPCs, after mirrorWait for statics
    // that may still be streaming in).
    private func Resolve(proxy: ref<CP2077CoopNpcProxy>, now: Float) -> Void {
        if EntityID.IsDefined(proxy.localId) || proxy.spawnRequested {
            return;
        }

        if proxy.worldId != 0ul {
            let localId: EntityID = EntityID.FromHash(proxy.worldId);
            let local: ref<GameObject> = GameInstance.FindEntityByID(GetGameInstance(), localId) as GameObject;
            if IsDefined(local) {
                proxy.localId = localId;
                proxy.isMirror = true;
                this.Unhide(local);
                if NotEquals(local.GetCurrentAppearanceName(), proxy.appearance) && IsNameValid(proxy.appearance) {
                    local.ScheduleAppearanceChange(proxy.appearance);
                }
                return;
            }
            if now - proxy.bindTime < this.mirrorWait {
                return;
            }
        }

        this.SpawnProxy(proxy);
    }

    private func SpawnProxy(proxy: ref<CP2077CoopNpcProxy>) -> Void {
        let dynamicEntities = GameInstance.GetDynamicEntitySystem();
        if !IsDefined(dynamicEntities) || !dynamicEntities.IsReady() || !TDBID.IsValid(proxy.record) {
            return;
        }

        let position: Vector4 = proxy.hasState ? proxy.lastState.position : proxy.bindPosition;
        let yaw: Float = proxy.hasState ? proxy.lastState.yaw : proxy.bindYaw;

        let angles: EulerAngles;
        angles.Yaw = yaw;

        let spec = new DynamicEntitySpec();
        spec.recordID = proxy.record;
        spec.appearanceName = proxy.appearance;
        spec.position = position;
        spec.orientation = EulerAngles.ToQuat(angles);
        spec.persistState = false;
        spec.persistSpawn = false;
        spec.alwaysSpawned = true;
        spec.spawnInView = true;
        spec.active = true;
        spec.tags = [CP2077CoopNpc_ProxyTag()];

        proxy.localId = dynamicEntities.CreateEntity(spec);
        proxy.isMirror = false;
        proxy.spawnRequested = true;
    }

    // Switches off the local copy's own perception so it cannot start fights,
    // flee or call police by itself; attitude towards the joiner follows the host.
    private func Prepare(proxy: ref<CP2077CoopNpcProxy>, npc: ref<NPCPuppet>, player: ref<PlayerPuppet>) -> Void {
        let senses: ref<SenseComponent> = npc.GetSensesComponent();
        if IsDefined(senses) {
            senses.ToggleComponent(false);
        }
        this.ApplyAttitude(proxy, npc, player, proxy.hostile);
        proxy.prepared = true;
    }

    private func ApplyAttitude(proxy: ref<CP2077CoopNpcProxy>, npc: ref<NPCPuppet>, player: ref<PlayerPuppet>, hostile: Bool) -> Void {
        let mine: ref<AttitudeAgent> = npc.GetAttitudeAgent();
        let theirs: ref<AttitudeAgent> = player.GetAttitudeAgent();
        if IsDefined(mine) && IsDefined(theirs) {
            mine.SetAttitudeTowards(theirs, hostile ? EAIAttitude.AIA_Hostile : EAIAttitude.AIA_Neutral);
        }
        proxy.hostile = hostile;
    }

    // NU1 <netId> <reason>
    public func OnUnbind(message: String) -> Void {
        let fields: array<String> = StrSplit(message, " ");
        if ArraySize(fields) < 2 || NotEquals(fields[0], "NU1") {
            return;
        }
        let proxy = this.FindByNetId(StringToInt(fields[1], 0));
        if IsDefined(proxy) {
            this.Release(proxy, true);
        }
    }

    private func Release(proxy: ref<CP2077CoopNpcProxy>, remove: Bool) -> Void {
        if proxy.isMirror {
            let local: ref<NPCPuppet> = this.GetLocal(proxy);
            if IsDefined(local) && !local.IsDead() {
                let senses: ref<SenseComponent> = local.GetSensesComponent();
                if IsDefined(senses) {
                    senses.ToggleComponent(true);
                }
            }
        } else {
            let dynamicEntities = GameInstance.GetDynamicEntitySystem();
            if IsDefined(dynamicEntities) && EntityID.IsDefined(proxy.localId) {
                dynamicEntities.DeleteEntity(proxy.localId);
            }
        }
        if remove {
            ArrayRemove(this.m_proxies, proxy);
        }
    }

    public func ReleaseAll() -> Void {
        while ArraySize(this.m_proxies) > 0 {
            this.Release(this.m_proxies[0], true);
        }
        this.m_hasSeq = false;
    }

    // ND1 <netId> <killer> <flags>
    public func OnDeath(message: String) -> Void {
        let fields: array<String> = StrSplit(message, " ");
        if ArraySize(fields) < 4 || NotEquals(fields[0], "ND1") {
            return;
        }
        let proxy = this.FindByNetId(StringToInt(fields[1], 0));
        if !IsDefined(proxy) {
            return;
        }
        let npc: ref<NPCPuppet> = this.GetLocal(proxy);
        if IsDefined(npc) {
            this.ApplyDeath(proxy, npc, (StringToInt(fields[3], 0) & 1) != 0);
        } else {
            proxy.dead = true;
        }
    }

    private func ApplyDeath(proxy: ref<CP2077CoopNpcProxy>, npc: ref<NPCPuppet>, ragdoll: Bool) -> Void {
        if proxy.dead && npc.IsDead() {
            return;
        }
        proxy.dead = true;
        if !npc.IsDead() {
            npc.Kill(null, false, false);
        }
        if ragdoll {
            npc.QueueEvent(CreateForceRagdollEvent(n"CP2077Coop host death"));
        }
    }

    // ------------------------------------------------------------
    // VEHICLES (called every frame from Update)
    // ------------------------------------------------------------

    // Dead-reckoning: last host position + velocity * age (age capped), approached
    // smoothly; teleport straight to it on large errors. Teleporting a physics
    // vehicle every frame is the same technique the current remote-player car uses;
    // a kinematic mover in the plugin would be the long-term replacement.
    private func UpdateVehicle(proxy: ref<CP2077CoopNpcProxy>, now: Float) -> Void {
        if !proxy.hasState {
            return;
        }
        let vehicle: ref<VehicleObject> = this.GetLocalObject(proxy) as VehicleObject;
        if !IsDefined(vehicle) {
            return;
        }

        let state: CP2077CoopNpcState = proxy.lastState;
        let age: Float = ClampF(now - proxy.lastReceiveTime, 0.0, 0.35);
        let target: Vector4 = state.position;
        target.X += state.velocity.X * age;
        target.Y += state.velocity.Y * age;
        target.Z += state.velocity.Z * age;

        let current: Vector4 = vehicle.GetWorldPosition();
        let error: Float = Vector4.Distance(current, target);
        let position: Vector4 = target;
        let yaw: Float = state.yaw;

        if error < 8.0 {
            let blend: Float = 0.35;
            position = new Vector4(
                current.X + (target.X - current.X) * blend,
                current.Y + (target.Y - current.Y) * blend,
                current.Z + (target.Z - current.Z) * blend,
                1.0
            );
            yaw = vehicle.GetWorldYaw() + AngleNormalize180(state.yaw - vehicle.GetWorldYaw()) * blend;
        }

        // text v1 carries yaw only: keep the local pitch/roll (slopes, suspension)
        let rotation: EulerAngles = Quaternion.ToEulerAngles(vehicle.GetWorldOrientation());
        rotation.Yaw = yaw;
        GameInstance.GetTeleportationFacility(vehicle.GetGame()).Teleport(vehicle, position, rotation);
    }

    // ------------------------------------------------------------
    // SNAPSHOTS
    // ------------------------------------------------------------

    // Applies one NS1 message. Older-than-latest sequences are dropped (16-bit wrap aware),
    // parts of the same sequence are all applied.
    public func OnSnapshot(message: String, player: ref<PlayerPuppet>, now: Float) -> Int32 {
        let seq: Int32;
        let states: array<CP2077CoopNpcState>;
        if !CP2077CoopNpc_DecodeSnapshot(message, seq, states) {
            return -1;
        }

        if this.m_hasSeq {
            let age: Int32 = (this.m_lastSeq - seq + 65536) % 65536;
            if age > 0 && age < 32768 {
                return 0;
            }
        }
        this.m_lastSeq = seq;
        this.m_hasSeq = true;

        let applied: Int32 = 0;
        let i: Int32 = 0;
        while i < ArraySize(states) {
            if this.ApplyState(states[i], player, now) {
                applied += 1;
            }
            i += 1;
        }
        return applied;
    }

    // Per-tick housekeeping: resolve pending mirrors/proxies, prepare newly spawned ones.
    public func Update(player: ref<PlayerPuppet>, now: Float) -> Void {
        let i: Int32 = 0;
        while i < ArraySize(this.m_proxies) {
            let proxy: ref<CP2077CoopNpcProxy> = this.m_proxies[i];
            this.Resolve(proxy, now);
            if CP2077CoopNpcJoiner.IsVehicleKind(proxy) {
                this.UpdateVehicle(proxy, now);
            } else if !proxy.prepared {
                let npc: ref<NPCPuppet> = this.GetLocal(proxy);
                if IsDefined(npc) {
                    this.Prepare(proxy, npc, player);
                    if proxy.dead {
                        this.ApplyDeath(proxy, npc, false);
                    }
                }
            }
            i += 1;
        }
        this.SweepLocalPopulation(player, now);
    }

    private func ApplyState(state: CP2077CoopNpcState, player: ref<PlayerPuppet>, now: Float) -> Bool {
        let proxy: ref<CP2077CoopNpcProxy> = this.FindByNetId(state.netId);
        if !IsDefined(proxy) {
            return false;
        }
        proxy.lastState = state;
        proxy.hasState = true;
        proxy.lastReceiveTime = now;

        if CP2077CoopNpcJoiner.IsVehicleKind(proxy) {
            // driven every frame by UpdateVehicle
            return true;
        }

        let npc: ref<NPCPuppet> = this.GetLocal(proxy);
        if !IsDefined(npc) {
            return false;
        }
        if !proxy.prepared {
            this.Prepare(proxy, npc, player);
        }

        if CP2077CoopNpc_HasFlag(state.flags, CP2077CoopNpcFlag_Dead()) {
            this.ApplyDeath(proxy, npc, false);
            return true;
        }
        if proxy.dead || npc.IsDead() {
            return true;
        }

        let hostile: Bool = CP2077CoopNpc_HasFlag(state.flags, CP2077CoopNpcFlag_Hostile());
        if NotEquals(hostile, proxy.hostile) {
            this.ApplyAttitude(proxy, npc, player, hostile);
        }

        this.ApplyStance(proxy, npc, CP2077CoopNpc_HasFlag(state.flags, CP2077CoopNpcFlag_Crouched()));
        this.ApplyWeapon(proxy, npc, CP2077CoopNpc_HasFlag(state.flags, CP2077CoopNpcFlag_WeaponDrawn()));
        this.ApplyMovement(proxy, npc, state, now);
        this.ApplyHealth(npc, state.health);
        return true;
    }

    private func ApplyStance(proxy: ref<CP2077CoopNpcProxy>, npc: ref<NPCPuppet>, crouched: Bool) -> Void {
        if Equals(crouched, proxy.crouched) {
            return;
        }
        proxy.crouched = crouched;
        NPCPuppet.ChangeStanceState(npc, crouched ? gamedataNPCStanceState.Crouch : gamedataNPCStanceState.Stand);
    }

    private func ApplyWeapon(proxy: ref<CP2077CoopNpcProxy>, npc: ref<NPCPuppet>, drawn: Bool) -> Void {
        if Equals(drawn, proxy.weaponDrawn) {
            return;
        }
        proxy.weaponDrawn = drawn;
        let ai: ref<AIHumanComponent> = npc.GetAIControllerComponent();
        if !IsDefined(ai) {
            return;
        }
        if drawn {
            ai.SendCommand(new AISwitchToPrimaryWeaponCommand());
        } else {
            let holster = new AIUnequipCommand();
            holster.slotId = t"AttachmentSlots.WeaponRight";
            ai.SendCommand(holster);
        }
    }

    private func ApplyHealth(npc: ref<NPCPuppet>, health: Float) -> Void {
        let pools: ref<StatPoolsSystem> = GameInstance.GetStatPoolsSystem(npc.GetGame());
        let id: StatsObjectID = Cast<StatsObjectID>(npc.GetEntityID());
        let current: Float = pools.GetStatPoolValue(id, gamedataStatPoolType.Health, true) / 100.0;
        if AbsF(current - health) > 0.03 {
            // never 0 here: deaths only come through ND1 / the Dead flag
            pools.RequestSettingStatPoolValue(id, gamedataStatPoolType.Health, MaxF(health * 100.0, 1.0), null, true);
        }
    }

    public static func MovementTypeFor(moveState: Int32) -> moveMovementType {
        if moveState == EnumInt(CP2077CoopMoveState.Sprint) {
            return moveMovementType.Sprint;
        }
        if moveState == EnumInt(CP2077CoopMoveState.Run) {
            return moveMovementType.Run;
        }
        return moveMovementType.Walk;
    }

    private func ApplyMovement(proxy: ref<CP2077CoopNpcProxy>, npc: ref<NPCPuppet>, state: CP2077CoopNpcState, now: Float) -> Void {
        // mirrors sitting in the same workspot on both sides: leave the animation alone
        if proxy.isMirror && CP2077CoopNpc_HasFlag(state.flags, CP2077CoopNpcFlag_Workspot())
            && GameInstance.GetWorkspotSystem(npc.GetGame()).IsActorInWorkspot(npc)
            && Vector4.Distance(npc.GetWorldPosition(), state.position) < 1.0 {
            return;
        }
        // NPCs inside vehicles follow the vehicle (vehicle sync), not their own transform
        if CP2077CoopNpc_HasFlag(state.flags, CP2077CoopNpcFlag_Mounted()) {
            return;
        }

        let ai: ref<AIHumanComponent> = npc.GetAIControllerComponent();
        if !IsDefined(ai) {
            return;
        }

        let current: Vector4 = npc.GetWorldPosition();
        let error: Float = Vector4.Distance(current, state.position);
        let moving: Bool = state.moveState == EnumInt(CP2077CoopMoveState.Walk)
            || state.moveState == EnumInt(CP2077CoopMoveState.Run)
            || state.moveState == EnumInt(CP2077CoopMoveState.Sprint)
            || state.moveState == EnumInt(CP2077CoopMoveState.CrouchMove);

        if error > this.teleportDistance || CP2077CoopNpc_HasFlag(state.flags, CP2077CoopNpcFlag_Teleported()) {
            this.CancelMove(proxy, ai);
            let teleport = new AITeleportCommand();
            teleport.position = state.position;
            teleport.rotation = state.yaw;
            teleport.doNavTest = false;
            ai.SendCommand(teleport);
            proxy.lastMoveTime = now;
            return;
        }

        if moving || error > 0.75 {
            let destination: Vector4 = state.position;
            destination.X += state.velocity.X * this.moveLead;
            destination.Y += state.velocity.Y * this.moveLead;
            // re-issuing too often cancels the locomotion start and makes NPCs stutter
            if Vector4.Distance(destination, proxy.lastMoveTarget) < 0.5 && now - proxy.lastMoveTime < 1.0 {
                return;
            }
            this.CancelMove(proxy, ai);

            let target: WorldPosition;
            WorldPosition.SetVector4(target, destination);
            let spec: AIPositionSpec;
            AIPositionSpec.SetWorldPosition(spec, target);

            let move = new AIMoveToCommand();
            move.movementTarget = spec;
            move.rotateEntityTowardsFacingTarget = false;
            move.ignoreNavigation = false;
            move.desiredDistanceFromTarget = 0.1;
            move.movementType = CP2077CoopNpcJoiner.MovementTypeFor(state.moveState);
            move.finishWhenDestinationReached = true;
            move.ignoreInCombat = false;
            move.removeAfterCombat = false;
            move.alwaysUseStealth = state.moveState == EnumInt(CP2077CoopMoveState.CrouchMove);
            ai.SendCommand(move);

            proxy.moveCommand = move;
            proxy.lastMoveTarget = destination;
            proxy.lastMoveTime = now;
            return;
        }

        // idle and in place: only fix the facing
        let yawError: Float = AbsF(AngleNormalize180(npc.GetWorldYaw() - state.yaw));
        if yawError > 25.0 && now - proxy.lastRotateTime > 0.5 {
            // same yaw convention as Entity.GetWorldYaw / Vector4.Heading
            let heading: Vector4 = Vector4.FromHeading(state.yaw);
            let facing: Vector4 = current;
            facing.X += heading.X * 5.0;
            facing.Y += heading.Y * 5.0;
            let facingPosition: WorldPosition;
            WorldPosition.SetVector4(facingPosition, facing);
            let facingSpec: AIPositionSpec;
            AIPositionSpec.SetWorldPosition(facingSpec, facingPosition);

            let rotate = new AIRotateToCommand();
            rotate.target = facingSpec;
            rotate.angleTolerance = 10.0;
            rotate.angleOffset = 0.0;
            rotate.speed = 1.0;
            ai.SendCommand(rotate);
            proxy.lastRotateTime = now;
        }
    }

    private func CancelMove(proxy: ref<CP2077CoopNpcProxy>, ai: ref<AIHumanComponent>) -> Void {
        if IsDefined(proxy.moveCommand) {
            ai.StopExecutingCommand(proxy.moveCommand, true);
            ai.CancelCommand(proxy.moveCommand);
            proxy.moveCommand = null;
        }
    }
}
