// Isolated, explicitly spawned test actor. Does not install the broad NPC prototype.
// This tag is private to the harness. No existing NPC is found or modified by ID.
public class CP2077CoopTestNpcLifecycle extends ScriptableSystem {
    private let ownedID: EntityID;
    private let retiringActor: wref<NPCPuppet>;
    private let retiring: Bool;
    private let deleteRequested: Bool;
    private let moveCommand: ref<AITeleportCommand>;
    private let moveActor: wref<NPCPuppet>;
    private let moveCancelRequested: Bool;

    public func MoveState() -> Int32 {
        return IsDefined(this.moveCommand) ? EnumInt(this.moveCommand.state) : -1;
    }

    public func CancelMove() -> Void {
        if !IsDefined(this.moveCommand) || this.moveCancelRequested {
            return;
        }
        let state = this.MoveState();
        if state >= 0 && state <= 2 && IsDefined(this.moveActor) {
            let controller = this.moveActor.GetAIControllerComponent();
            if IsDefined(controller) {
                // Use the controller's cancellation API; do not mark a timed-out
                // teleport successful with StopExecutingCommand(..., true).
                controller.CancelCommand(this.moveCommand);
            }
        }
        this.moveCancelRequested = true;
    }

    public func RequestMove(actor: ref<NPCPuppet>, x: Float, y: Float, z: Float, yaw: Float) -> Bool {
        if this.retiring || (this.MoveState() >= 0 && this.MoveState() <= 2) {
            return false;
        }
        let controller = actor.GetAIControllerComponent();
        if !IsDefined(controller) {
            return false;
        }
        let command = new AITeleportCommand();
        command.position = new Vector4(x, y, z, 1.0);
        command.rotation = yaw;
        command.doNavTest = false;
        if !controller.SendCommand(command) {
            return false; // no pending handle for a command the engine refused
        }
        this.moveCommand = command;
        this.moveActor = actor;
        this.moveCancelRequested = false;
        return true;
    }

    public func Track(id: EntityID) -> Void {
        this.ownedID = id;
        this.retiring = false;
        this.deleteRequested = false;
    }

    public func GetActor() -> ref<NPCPuppet> {
        let system = GameInstance.GetDynamicEntitySystem();
        if !IsDefined(system) || !system.IsReady() || this.retiring {
            return null;
        }
        return system.GetEntity(this.ownedID) as NPCPuppet;
    }

    public func Exists() -> Bool {
        let system = GameInstance.GetDynamicEntitySystem();
        if !EntityID.IsDefined(this.ownedID) {
            return false;
        }
        // During world teardown absence cannot be inferred from an unavailable API.
        if !IsDefined(system) || !system.IsReady() {
            return true;
        }
        let actor = system.GetEntity(this.ownedID) as NPCPuppet;
        return system.IsManaged(this.ownedID) || system.IsSpawning(this.ownedID)
            || (IsDefined(actor) && (!this.deleteRequested || actor.IsAttached()))
            || (IsDefined(this.retiringActor) && this.retiringActor.IsAttached());
    }

    public func ClearOwned(force: Bool) -> Void {
        this.CancelMove();
        let system = GameInstance.GetDynamicEntitySystem();
        if !IsDefined(system) || !system.IsReady() {
            return;
        }
        if !EntityID.IsDefined(this.ownedID) {
            // Only recover our private ID after a script reload; never accept IDs from Lua/network.
            this.ownedID = system.GetTaggedID(n"CP2077Coop.TestNpc");
        }
        this.retiring = true;
        let actor = system.GetEntity(this.ownedID) as NPCPuppet;
        if IsDefined(actor) {
            this.retiringActor = actor;
        }
        // CreateEntity's stub callback is asynchronous. Keep its managed record
        // until an entity exists, so deletion cannot lose an in-flight create.
        if force || IsDefined(actor) {
            if system.DeleteEntity(this.ownedID) {
                this.deleteRequested = true;
            }
        }
        if force || !this.Exists() {
            let empty: EntityID;
            this.ownedID = empty;
            this.retiringActor = null;
            this.retiring = false;
            this.deleteRequested = false;
            this.moveCommand = null;
            this.moveActor = null;
            this.moveCancelRequested = false;
        }
    }

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
            if this.retiring && Equals(actor.GetEntityID(), this.ownedID) {
                this.ClearOwned(false);
            }
        }
    }

    private cb func OnSessionBeforeEnd(event: ref<GameSessionEvent>) {
        this.ClearOwned(true);
    }

    private func OnDetach() -> Void {
        this.ClearOwned(true);
    }
}

public func CP2077CoopTestNpcClear() -> Void {
    let lifecycle = GameInstance.GetScriptableSystemsContainer(GetGameInstance()).Get(n"CP2077CoopTestNpcLifecycle") as CP2077CoopTestNpcLifecycle;
    if IsDefined(lifecycle) {
        lifecycle.ClearOwned(false);
    }
}

@addMethod(PlayerPuppet)
public func CP2077Coop_TestNpcClear() -> Void {
    CP2077CoopTestNpcClear();
}

@addMethod(PlayerPuppet)
public func CP2077Coop_TestNpcGet() -> ref<NPCPuppet> {
    let lifecycle = GameInstance.GetScriptableSystemsContainer(this.GetGame()).Get(n"CP2077CoopTestNpcLifecycle") as CP2077CoopTestNpcLifecycle;
    return IsDefined(lifecycle) ? lifecycle.GetActor() : null;
}

@addMethod(PlayerPuppet)
public func CP2077Coop_TestNpcExists() -> Bool {
    let lifecycle = GameInstance.GetScriptableSystemsContainer(this.GetGame()).Get(n"CP2077CoopTestNpcLifecycle") as CP2077CoopTestNpcLifecycle;
    return IsDefined(lifecycle) && lifecycle.Exists();
}

@addMethod(PlayerPuppet)
public func CP2077Coop_TestNpcMoveState() -> Int32 {
    let lifecycle = GameInstance.GetScriptableSystemsContainer(this.GetGame()).Get(n"CP2077CoopTestNpcLifecycle") as CP2077CoopTestNpcLifecycle;
    return IsDefined(lifecycle) ? lifecycle.MoveState() : -1;
}

@addMethod(PlayerPuppet)
public func CP2077Coop_TestNpcCancelMove() -> Void {
    let lifecycle = GameInstance.GetScriptableSystemsContainer(this.GetGame()).Get(n"CP2077CoopTestNpcLifecycle") as CP2077CoopTestNpcLifecycle;
    if IsDefined(lifecycle) {
        lifecycle.CancelMove();
    }
}

@addMethod(PlayerPuppet)
public func CP2077Coop_TestNpcSpawn(x: Float, y: Float, z: Float, yaw: Float) -> Bool {
    let system = GameInstance.GetDynamicEntitySystem();
    if !IsDefined(system) || !system.IsReady() {
        return false;
    }
    // Fixed generic record from Codeware's documented spawning example. Never
    // accept a remote record ID, quest entity ID, appearance path or arbitrary tag.
    let record = t"Character.spr_animals_bouncer1_ranged1_omaha_mb";
    if !IsDefined(TweakDBInterface.GetCharacterRecord(record)) {
        return false;
    }
    // Instantiate the lifecycle system only for an explicitly requested actor.
    // Its BeforeEnd callback must exist before this temporary entity is created.
    let lifecycle = GameInstance.GetScriptableSystemsContainer(this.GetGame()).Get(n"CP2077CoopTestNpcLifecycle") as CP2077CoopTestNpcLifecycle;
    if !IsDefined(lifecycle) || lifecycle.Exists() {
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
    let id = system.CreateEntity(spec);
    if !EntityID.IsDefined(id) {
        return false;
    }
    lifecycle.Track(id);
    return true;
}

@addMethod(PlayerPuppet)
public func CP2077Coop_TestNpcMove(x: Float, y: Float, z: Float, yaw: Float) -> Bool {
    let actor = this.CP2077Coop_TestNpcGet();
    if !IsDefined(actor) || !actor.IsAttached() || actor.IsDead() {
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
    let lifecycle = GameInstance.GetScriptableSystemsContainer(this.GetGame()).Get(n"CP2077CoopTestNpcLifecycle") as CP2077CoopTestNpcLifecycle;
    return IsDefined(lifecycle) && lifecycle.RequestMove(actor, x, y, z, yaw);
}
