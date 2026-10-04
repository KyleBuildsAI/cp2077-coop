// Isolated, explicitly spawned test actor. Does not install the broad NPC prototype.
// This tag is private to the harness. No existing NPC is found or modified by ID.
public class CP2077CoopTestNpcLifecycle extends ScriptableSystem {
    private func OnAttach() -> Void {
        GameInstance.GetCallbackSystem().RegisterCallback(n"Session/BeforeEnd", this, n"OnSessionBeforeEnd");
        GameInstance.GetCallbackSystem().RegisterCallback(n"Entity/Attach", this, n"OnTestActorAttach")
            .AddTarget(DynamicEntityTarget.Tag(n"CP2077Coop.TestNpc"));
    }

    private cb func OnTestActorAttach(event: ref<EntityLifecycleEvent>) {
        let actor = event.GetEntity() as NPCPuppet;
        if IsDefined(actor) {
            let senses = actor.GetSensesComponent();
            if IsDefined(senses) {
                senses.ToggleComponent(false);
            }
        }
    }

    private cb func OnSessionBeforeEnd(event: ref<GameSessionEvent>) {
        CP2077CoopTestNpcClear();
    }

    private func OnDetach() -> Void {
        CP2077CoopTestNpcClear();
    }
}

public func CP2077CoopTestNpcClear() -> Void {
    let system = GameInstance.GetDynamicEntitySystem();
    if IsDefined(system) && system.IsReady() {
        system.DeleteTagged(n"CP2077Coop.TestNpc");
    }
}

@addMethod(PlayerPuppet)
public func CP2077Coop_TestNpcClear() -> Void {
    CP2077CoopTestNpcClear();
}

@addMethod(PlayerPuppet)
public func CP2077Coop_TestNpcGet() -> ref<NPCPuppet> {
    let system = GameInstance.GetDynamicEntitySystem();
    if !IsDefined(system) || !system.IsReady() {
        return null;
    }
    let entities = system.GetTagged(n"CP2077Coop.TestNpc");
    return ArraySize(entities) > 0 ? entities[0] as NPCPuppet : null;
}

@addMethod(PlayerPuppet)
public func CP2077Coop_TestNpcSpawn(x: Float, y: Float, z: Float, yaw: Float) -> Bool {
    let system = GameInstance.GetDynamicEntitySystem();
    if !IsDefined(system) || !system.IsReady() {
        return false;
    }
    if system.IsPopulated(n"CP2077Coop.TestNpc") {
        return true;
    }
    // Fixed generic record from Codeware's documented spawning example. Never
    // accept a remote record ID, quest entity ID, appearance path or arbitrary tag.
    let record = t"Character.spr_animals_bouncer1_ranged1_omaha_mb";
    if !IsDefined(TweakDBInterface.GetCharacterRecord(record)) {
        return false;
    }
    // Instantiate the lifecycle system only for an explicitly requested actor.
    // Its BeforeEnd callback must exist before this temporary entity is created.
    let lifecycle = GameInstance.GetScriptableSystemsContainer(this.GetGame()).Get(n"CP2077CoopTestNpcLifecycle");
    if !IsDefined(lifecycle) {
        return false;
    }
    let angles: EulerAngles;
    angles.Yaw = yaw;
    let spec = new DynamicEntitySpec();
    spec.recordID = record;
    spec.position = new Vector4(x, y, z, 1.0);
    spec.orientation = EulerAngles.ToQuat(angles);
    spec.persistState = false;
    spec.persistSpawn = false;
    spec.alwaysSpawned = true;
    spec.spawnInView = true;
    spec.active = true;
    spec.tags = [n"CP2077Coop.TestNpc"];
    return EntityID.IsDefined(system.CreateEntity(spec));
}

@addMethod(PlayerPuppet)
public func CP2077Coop_TestNpcMove(x: Float, y: Float, z: Float, yaw: Float) -> Bool {
    let actor = this.CP2077Coop_TestNpcGet();
    if !IsDefined(actor) || actor.IsDead() {
        return false;
    }
    let senses = actor.GetSensesComponent();
    if IsDefined(senses) {
        senses.ToggleComponent(false);
    }
    let attitude = actor.GetAttitudeAgent();
    let playerAttitude = this.GetAttitudeAgent();
    if IsDefined(attitude) && IsDefined(playerAttitude) {
        attitude.SetAttitudeTowards(playerAttitude, EAIAttitude.AIA_Neutral);
    }
    let angles: EulerAngles;
    angles.Yaw = yaw;
    GameInstance.GetTeleportationFacility(this.GetGame()).Teleport(actor, new Vector4(x, y, z, 1.0), angles);
    return true;
}
