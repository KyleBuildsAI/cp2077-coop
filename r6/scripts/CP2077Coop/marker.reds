// One transient marker for the partner's last received PLAYER position.
// Do not attach it to the stand-in NPC: that entity can lag, despawn at range,
// or be hidden while the partner drives. The Lua receiver owns freshness.
@addField(PlayerPuppet)
private let CP2077Coop_RemoteMappin: NewMappinID;

@addField(PlayerPuppet)
private let CP2077Coop_RemoteMappinRegistered: Bool;

@addMethod(PlayerPuppet)
public func CP2077Coop_ShowRemoteMarker(x: Float, y: Float, z: Float) -> Bool {
    let system = GameInstance.GetMappinSystem(this.GetGame());
    if !IsDefined(system) {
        return false;
    }

    let position = new Vector4(x, y, z, 1.0);
    if this.CP2077Coop_RemoteMappinRegistered {
        system.SetMappinPosition(this.CP2077Coop_RemoteMappin, position);
    } else {
        let data: MappinData;
        // CPO_RemotePlayerVariant routes to a multiplayer-only controller that
        // casts this generic mappin to RemotePlayerMappin and reads its vitals.
        // The regular custom-position pin works with RegisterMappin in retail.
        // Registering our own ID never changes the player's tracked waypoint.
        data.mappinType = t"Mappins.CustomPositionMappinDefinition";
        data.variant = gamedataMappinVariant.CustomPositionVariant;
        data.debugCaption = "Co-op partner";
        data.visibleThroughWalls = true;
        this.CP2077Coop_RemoteMappin = system.RegisterMappin(data, position);
        this.CP2077Coop_RemoteMappinRegistered = true;
    }
    return true;
}

@addMethod(PlayerPuppet)
public func CP2077Coop_HideRemoteMarker() -> Void {
    if !this.CP2077Coop_RemoteMappinRegistered {
        return;
    }
    let system = GameInstance.GetMappinSystem(this.GetGame());
    if IsDefined(system) {
        system.UnregisterMappin(this.CP2077Coop_RemoteMappin);
    }
    this.CP2077Coop_RemoteMappinRegistered = false;
}

// The Lua player handle may already be gone when it notices a save unload.
// Remove the marker while the old player's game instance is still available.
@wrapMethod(PlayerPuppet)
protected cb func OnDetach() -> Bool {
    this.CP2077Coop_HideRemoteMarker();
    return wrappedMethod();
}
