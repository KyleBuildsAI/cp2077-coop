@addMethod(PlayerPuppet)
public func CP2077Coop_SpawnRemoteTest() -> Void {
    let system = GameInstance.GetDynamicEntitySystem();

    if !IsDefined(system) || !system.IsReady() {
        return;
    }

    if system.IsPopulated(n"CP2077Coop.Remote") {
        return;
    }

    let position = this.GetWorldPosition();
    let forward = this.GetWorldForward();

    // 2.5 m przed lokalnym graczem
    position.X += forward.X * 2.5;
    position.Y += forward.Y * 2.5;

    let spec = new DynamicEntitySpec();

    spec.recordID = t"Character.Judy";
    spec.position = position;
    spec.orientation = this.GetWorldOrientation();

    spec.persistState = false;
    spec.persistSpawn = false;

    spec.alwaysSpawned = true;
    spec.spawnInView = true;
    spec.active = true;

    spec.tags = [
        n"CP2077Coop.Remote"
    ];

    system.CreateEntity(spec);
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