// CP2077Coop NPC sync - system, entity registry, damage routing and the
// PlayerPuppet API that CET Lua (init.lua) calls.
//
// Lua drives it (role, ticks) and moves strings between it and the transport:
//
//   host, 10 Hz:   player:CP2077Coop_NpcHostTick()
//                  for _, m in ipairs(player:CP2077Coop_NpcTakeReliable())   do Game.Net_Send(RELIABLE_CH, m) end
//                  for _, m in ipairs(player:CP2077Coop_NpcTakeUnreliable()) do Game.Net_Send(UNRELIABLE_CH, m) end
//   joiner, every frame: player:CP2077Coop_NpcJoinerUpdate()  (+ send TakeReliable: hit reports)
//   both, on receive:
//                  NB1 -> player:CP2077Coop_NpcReceiveBind(m, TweakDBID.new(recHash, recLen))
//                  else -> player:CP2077Coop_NpcReceive(m)


public func CP2077CoopNpcRole_Off() -> Int32 { return 0; }
public func CP2077CoopNpcRole_Host() -> Int32 { return 1; }
public func CP2077CoopNpcRole_Joiner() -> Int32 { return 2; }

public class CP2077CoopNpcSync extends ScriptableSystem {
    private let m_role: Int32;
    private let m_host: ref<CP2077CoopNpcHost>;
    private let m_joiner: ref<CP2077CoopNpcJoiner>;
    private let m_vehicles: array<wref<VehicleObject>>;
    private let m_reliable: array<String>;
    private let m_unreliable: array<String>;

    private let m_hitsSent: Int32;
    private let m_hitsApplied: Int32;
    private let m_playerHitsSent: Int32;
    private let m_playerHitsTaken: Int32;
    private let m_snapshotsApplied: Int32;

    public static func Get(game: GameInstance) -> ref<CP2077CoopNpcSync> {
        return GameInstance.GetScriptableSystemsContainer(game).Get(n"CP2077CoopNpcSync") as CP2077CoopNpcSync;
    }

    private func OnAttach() -> Void {
        this.m_host = new CP2077CoopNpcHost();
        this.m_host.Configure(80.0, 40);
        this.m_host.Reset();
        this.m_joiner = new CP2077CoopNpcJoiner();
        this.m_joiner.Configure(40);

        // Codeware lifecycle callbacks: the targeting query only finds puppets, and
        // the joiner must hide population the moment it streams in, not a tick later.
        let callbacks: ref<CallbackSystem> = GameInstance.GetCallbackSystem();
        if IsDefined(callbacks) {
            callbacks.RegisterCallback(n"Entity/Attach", this, n"OnEntityAttach")
                .AddTarget(EntityTarget.Type(n"NPCPuppet"))
                .AddTarget(EntityTarget.Type(n"VehicleObject"))
                .AddTarget(EntityTarget.Type(n"vehicleBaseObject"));
        }
    }

    private cb func OnEntityAttach(event: ref<EntityLifecycleEvent>) {
        let object: ref<GameObject> = event.GetEntity() as GameObject;
        if !IsDefined(object) {
            return;
        }

        let vehicle: ref<VehicleObject> = object as VehicleObject;
        if IsDefined(vehicle) {
            this.TrackVehicle(vehicle);
        }

        if this.m_role == CP2077CoopNpcRole_Joiner() && this.m_joiner.ShouldSuppress(object) {
            this.m_joiner.Hide(object);
        }
    }

    private func TrackVehicle(vehicle: ref<VehicleObject>) -> Void {
        let i: Int32 = ArraySize(this.m_vehicles) - 1;
        while i >= 0 {
            let known: ref<VehicleObject> = this.m_vehicles[i];
            if !IsDefined(known) || !known.IsAttached() {
                ArrayErase(this.m_vehicles, i);
            } else {
                if known.GetEntityID() == vehicle.GetEntityID() {
                    return;
                }
            }
            i -= 1;
        }
        ArrayPush(this.m_vehicles, vehicle);
    }

    // ------------------------------------------------------------
    // ROLE / TICKS
    // ------------------------------------------------------------

    public func GetRole() -> Int32 {
        return this.m_role;
    }

    public func SetRole(player: ref<PlayerPuppet>, role: Int32) -> Void {
        if role == this.m_role {
            return;
        }
        if this.m_role == CP2077CoopNpcRole_Joiner() {
            this.m_joiner.ReleaseAll();
            this.m_joiner.SetPopulationSuppressed(player, false);
        }
        this.m_host.Reset();
        ArrayClear(this.m_reliable);
        ArrayClear(this.m_unreliable);
        this.m_role = role;
        if role == CP2077CoopNpcRole_Joiner() {
            this.m_joiner.SetPopulationSuppressed(player, true);
        }
    }

    public static func Now(game: GameInstance) -> Float {
        return EngineTime.ToFloat(GameInstance.GetSimTime(game));
    }

    public static func FindAvatar() -> ref<GameObject> {
        let dynamicEntities = GameInstance.GetDynamicEntitySystem();
        if !IsDefined(dynamicEntities) || !dynamicEntities.IsReady() {
            return null;
        }
        let tagged: array<ref<Entity>> = dynamicEntities.GetTagged(CP2077CoopNpc_AvatarTag());
        if ArraySize(tagged) == 0 {
            return null;
        }
        return tagged[0] as GameObject;
    }

    public func HostTick(player: ref<PlayerPuppet>) -> Void {
        if this.m_role != CP2077CoopNpcRole_Host() {
            return;
        }
        this.m_host.Tick(player, CP2077CoopNpcSync.FindAvatar(), this.m_vehicles, CP2077CoopNpcSync.Now(player.GetGame()), this.m_reliable, this.m_unreliable);
    }

    public func JoinerUpdate(player: ref<PlayerPuppet>) -> Void {
        if this.m_role != CP2077CoopNpcRole_Joiner() {
            return;
        }
        this.m_joiner.Update(player, CP2077CoopNpcSync.Now(player.GetGame()));
    }

    public func TakeReliable() -> array<String> {
        let messages: array<String> = this.m_reliable;
        ArrayClear(this.m_reliable);
        return messages;
    }

    public func TakeUnreliable() -> array<String> {
        let messages: array<String> = this.m_unreliable;
        ArrayClear(this.m_unreliable);
        return messages;
    }

    // ------------------------------------------------------------
    // RECEIVE
    // ------------------------------------------------------------

    public func Receive(player: ref<PlayerPuppet>, message: String) -> Bool {
        let now: Float = CP2077CoopNpcSync.Now(player.GetGame());

        if this.m_role == CP2077CoopNpcRole_Joiner() {
            if StrBeginsWith(message, "NS1 ") {
                if this.m_joiner.OnSnapshot(message, player, now) >= 0 {
                    this.m_snapshotsApplied += 1;
                    return true;
                }
                return false;
            }
            if StrBeginsWith(message, "NU1 ") {
                this.m_joiner.OnUnbind(message);
                return true;
            }
            if StrBeginsWith(message, "ND1 ") {
                this.m_joiner.OnDeath(message);
                return true;
            }
            if StrBeginsWith(message, "NI1 ") {
                this.m_joiner.OnCoverageIndex(message);
                return true;
            }
            if StrBeginsWith(message, "PD1 ") {
                return this.ApplyPlayerDamage(player, message);
            }
            return false;
        }

        if this.m_role == CP2077CoopNpcRole_Host() && StrBeginsWith(message, "NH1 ") {
            if this.m_host.ApplyJoinerHit(message, CP2077CoopNpcSync.FindAvatar()) {
                this.m_hitsApplied += 1;
                return true;
            }
        }
        return false;
    }

    public func ReceiveBind(player: ref<PlayerPuppet>, message: String, record: TweakDBID) -> Bool {
        if this.m_role != CP2077CoopNpcRole_Joiner() {
            return false;
        }
        return this.m_joiner.OnBind(message, record, CP2077CoopNpcSync.Now(player.GetGame()));
    }

    // PD1 <attacker> <damageX100> <attackType>: a host NPC hit the joiner's avatar.
    private func ApplyPlayerDamage(player: ref<PlayerPuppet>, message: String) -> Bool {
        let fields: array<String> = StrSplit(message, " ");
        if ArraySize(fields) < 4 {
            return false;
        }
        let damage: Float = ClampF(Cast<Float>(StringToInt(fields[2], 0)) / 100.0, 0.0, 100000.0);
        let game: GameInstance = player.GetGame();
        if damage <= 0.0 || GameInstance.GetGodModeSystem(game).HasGodMode(player.GetEntityID(), gameGodModeType.Invulnerable) {
            return false;
        }
        GameInstance.GetStatPoolsSystem(game).RequestChangingStatPoolValue(
            Cast<StatsObjectID>(player.GetEntityID()), gamedataStatPoolType.Health, -damage, null, false, false);
        this.m_playerHitsTaken += 1;
        return true;
    }

    // ------------------------------------------------------------
    // DAMAGE ROUTING (called from the DealDamages wrapper)
    // ------------------------------------------------------------

    // Returns true when the local damage must NOT be applied.
    //  joiner: hits on synced NPCs are reported to the host (NH1) and not applied
    //          locally; hits from synced NPCs on the joiner are dropped (the host
    //          reports real ones with PD1).
    //  host:   hits on the joiner's avatar are forwarded to the joiner (PD1).
    public func InterceptDamage(hitEvent: ref<gameHitEvent>) -> Bool {
        if this.m_role == CP2077CoopNpcRole_Off() || !IsDefined(hitEvent) || !IsDefined(hitEvent.target) || !IsDefined(hitEvent.attackData) {
            return false;
        }

        let target: ref<GameObject> = hitEvent.target;
        let instigator: ref<GameObject> = hitEvent.attackData.GetInstigator();
        let damage: Float = hitEvent.attackComputed.GetTotalAttackValue(gamedataStatPoolType.Health);
        let attackType: Int32 = EnumInt(hitEvent.attackData.GetAttackType());

        if this.m_role == CP2077CoopNpcRole_Joiner() {
            let proxy: ref<CP2077CoopNpcProxy> = this.m_joiner.FindByLocalId(target.GetEntityID());
            if IsDefined(proxy) {
                if IsDefined(instigator) && instigator.IsPlayer() && damage > 0.0 && !proxy.dead {
                    ArrayPush(this.m_reliable, "NH1 " + IntToString(proxy.netId) + " " + IntToString(RoundF(damage * 100.0))
                        + " " + IntToString(attackType) + " 0");
                    this.m_hitsSent += 1;
                }
                return true;
            }
            if target.IsPlayer() && IsDefined(instigator) && IsDefined(this.m_joiner.FindByLocalId(instigator.GetEntityID())) {
                return true;
            }
            return false;
        }

        if this.m_role == CP2077CoopNpcRole_Host() {
            let dynamicEntities = GameInstance.GetDynamicEntitySystem();
            if IsDefined(dynamicEntities) && dynamicEntities.IsTagged(target.GetEntityID(), CP2077CoopNpc_AvatarTag()) {
                if damage > 0.0 {
                    let attacker: Int32 = CP2077CoopNpcTarget_None();
                    if IsDefined(instigator) {
                        let entry = this.m_host.FindByEntityID(instigator.GetEntityID());
                        attacker = IsDefined(entry) ? entry.netId : (instigator.IsPlayer() ? CP2077CoopNpcTarget_Host() : CP2077CoopNpcTarget_None());
                    }
                    ArrayPush(this.m_reliable, "PD1 " + IntToString(attacker) + " " + IntToString(RoundF(damage * 100.0)) + " " + IntToString(attackType));
                    this.m_playerHitsSent += 1;
                }
                return true;
            }
        }
        return false;
    }

    public func Stats() -> String {
        return "role=" + IntToString(this.m_role)
            + " vehiclesTracked=" + IntToString(ArraySize(this.m_vehicles))
            + " snapshotsApplied=" + IntToString(this.m_snapshotsApplied)
            + " hitsSent=" + IntToString(this.m_hitsSent)
            + " hitsApplied=" + IntToString(this.m_hitsApplied)
            + " playerHitsSent=" + IntToString(this.m_playerHitsSent)
            + " playerHitsTaken=" + IntToString(this.m_playerHitsTaken);
    }
}


// ------------------------------------------------------------
// DAMAGE HOOK
// ------------------------------------------------------------

// DealDamages runs after every modifier (armor, crits, headshots, perks), so the
// value reported to the host is what the joiner's game would have applied.
// Hit reactions still play locally (ProcessHitReaction runs after Process).
@wrapMethod(DamageSystem)
private final func DealDamages(hitEvent: ref<gameHitEvent>) -> Void {
    let sync: ref<CP2077CoopNpcSync> = CP2077CoopNpcSync.Get(GetGameInstance());
    if IsDefined(sync) && sync.InterceptDamage(hitEvent) {
        return;
    }
    wrappedMethod(hitEvent);
}


// ------------------------------------------------------------
// LUA API (PlayerPuppet methods, like the rest of CP2077Coop)
// ------------------------------------------------------------

@addMethod(PlayerPuppet)
public func CP2077Coop_NpcSetRole(role: Int32) -> Void {
    let sync = CP2077CoopNpcSync.Get(this.GetGame());
    if IsDefined(sync) {
        sync.SetRole(this, role);
    }
}

@addMethod(PlayerPuppet)
public func CP2077Coop_NpcHostTick() -> Void {
    let sync = CP2077CoopNpcSync.Get(this.GetGame());
    if IsDefined(sync) {
        sync.HostTick(this);
    }
}

@addMethod(PlayerPuppet)
public func CP2077Coop_NpcJoinerUpdate() -> Void {
    let sync = CP2077CoopNpcSync.Get(this.GetGame());
    if IsDefined(sync) {
        sync.JoinerUpdate(this);
    }
}

@addMethod(PlayerPuppet)
public func CP2077Coop_NpcTakeReliable() -> array<String> {
    let sync = CP2077CoopNpcSync.Get(this.GetGame());
    let none: array<String>;
    return IsDefined(sync) ? sync.TakeReliable() : none;
}

@addMethod(PlayerPuppet)
public func CP2077Coop_NpcTakeUnreliable() -> array<String> {
    let sync = CP2077CoopNpcSync.Get(this.GetGame());
    let none: array<String>;
    return IsDefined(sync) ? sync.TakeUnreliable() : none;
}

@addMethod(PlayerPuppet)
public func CP2077Coop_NpcReceive(message: String) -> Bool {
    let sync = CP2077CoopNpcSync.Get(this.GetGame());
    return IsDefined(sync) && sync.Receive(this, message);
}

@addMethod(PlayerPuppet)
public func CP2077Coop_NpcReceiveBind(message: String, record: TweakDBID) -> Bool {
    let sync = CP2077CoopNpcSync.Get(this.GetGame());
    return IsDefined(sync) && sync.ReceiveBind(this, message, record);
}

@addMethod(PlayerPuppet)
public func CP2077Coop_NpcStats() -> String {
    let sync = CP2077CoopNpcSync.Get(this.GetGame());
    return IsDefined(sync) ? sync.Stats() : "npc sync unavailable";
}
