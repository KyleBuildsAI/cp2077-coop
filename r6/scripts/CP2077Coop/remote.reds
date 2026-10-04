// Spawn avatara w (x, y, z), patrzącego wzdłuż (forwardX, forwardY);
// wektor zerowy = jak lokalny gracz. Lua podaje pozycję drugiego gracza
// z pakietu (bez przewidywania), więc zaraz po spawnie nie trzeba
// AITeleportCommand, który czeka, aż AI nowego NPC ruszy (test na żywo
// 2026-10-04: REMOTE SNAP FAILED error=9.06 i NOT RESPONDING przy
// spawnie 2.5 m przed lokalnym graczem). Daleko od lokalnego gracza Lua
// podaje punkt 2.5 m przed nim, jak dawniej.
// true = avatar istnieje albo spawn zlecony; false = nic nie zlecono
// (system encji jeszcze niegotowy, świat się wczytuje): Lua ponowi
@addMethod(PlayerPuppet)
public func CP2077Coop_SpawnRemoteTest(x: Float, y: Float, z: Float, forwardX: Float, forwardY: Float) -> Bool {
    let system = GameInstance.GetDynamicEntitySystem();

    if !IsDefined(system) || !system.IsReady() {
        return false;
    }

    if system.IsPopulated(n"CP2077Coop.Remote") {
        return true;
    }

    let spec = new DynamicEntitySpec();

    spec.recordID = t"Character.Judy";
    spec.position = new Vector4(x, y, z, 1.0);

    let facing = new Vector4(forwardX, forwardY, 0.0, 0.0);

    if Vector4.Length2D(facing) > 0.01 {
        spec.orientation = EulerAngles.ToQuat(Vector4.ToRotation(facing));
    } else {
        spec.orientation = this.GetWorldOrientation();
    }

    spec.persistState = false;
    spec.persistSpawn = false;

    spec.alwaysSpawned = true;
    spec.spawnInView = true;
    spec.active = true;

    spec.tags = [
        n"CP2077Coop.Remote"
    ];

    return EntityID.IsDefined(system.CreateEntity(spec));
}


// Usuwa wpis avatara, który nie pojawił się w świecie (IsPopulated = true
// przy pustym GetTagged blokowałoby każdy kolejny spawn).
@addMethod(PlayerPuppet)
public func CP2077Coop_DespawnRemote() -> Void {
    let system = GameInstance.GetDynamicEntitySystem();

    if IsDefined(system) && system.IsReady() && system.IsPopulated(n"CP2077Coop.Remote") {
        system.DeleteTagged(n"CP2077Coop.Remote");
    }
}


// Opt-in retained-command experiment. AIMoveToCommandHandler reads the target
// on UpdateCommand; changing it here preserves the executing command handle.
// Return 2 only after updating an executing command, 0/1 while it is pending,
// 3..6 for terminal command states, and -1 when the handle is unavailable.
// Lua decides whether to wait or submit a replacement and never predicts here.
@addMethod(PlayerPuppet)
public func CP2077Coop_RetargetRemoteMove(command: ref<AIMoveToCommand>, x: Float, y: Float, z: Float) -> Int32 {
    if !IsDefined(command) {
        return -1;
    }

    if NotEquals(command.state, AICommandState.Executing) {
        return EnumInt(command.state);
    }

    let target: WorldPosition;
    WorldPosition.SetVector4(target, new Vector4(x, y, z, 1.0));
    let position: AIPositionSpec;
    AIPositionSpec.SetWorldPosition(position, target);
    command.movementTarget = position;
    return 2;
}


// forwardX/forwardY: kierunek drugiego gracza (avatar patrzy tak
// samo po teleporcie); wektor zerowy = obecny obrót avatara
@addMethod(PlayerPuppet)
public func CP2077Coop_MoveRemoteTest(
    x: Float,
    y: Float,
    z: Float,
    forwardX: Float,
    forwardY: Float
) -> Void {
    let system = GameInstance.GetDynamicEntitySystem();

    if !IsDefined(system) || !system.IsReady() {
        return;
    }

    let entities = system.GetTagged(n"CP2077Coop.Remote");

    if ArraySize(entities) == 0 {
        return;
    }

    let remote = entities[0] as NPCPuppet;

    if !IsDefined(remote) {
        return;
    }

    let controller = remote.GetAIControllerComponent();

    if !IsDefined(controller) {
        return;
    }

    let position: Vector4;

    position.X = x;
    position.Y = y;
    position.Z = z;
    position.W = 1.0;

    let command = new AITeleportCommand();

    command.position = position;

    // rotation to bezwzględny yaw świata w stopniach: 0.0 obracało
    // avatar na +Y (północ) przy każdej korekcie, dashu i skoku
    let facing = new Vector4(forwardX, forwardY, 0.0, 0.0);

    if Vector4.Length2D(facing) > 0.01 {
        command.rotation = Vector4.Heading(facing);
    } else {
        command.rotation = remote.GetWorldYaw();
    }

    command.doNavTest = false;

    controller.SendCommand(command);
}
