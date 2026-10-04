// CP2077Coop vehicle sync.
//
// Both players share this ordered list of player vehicles. The index of the
// local player's vehicle (1-based, 0 = not in list) travels over the network;
// the other side spawns the same vehicle and moves it with the remote player.
// Unknown vehicles (modded, stolen NPC cars) show the fallback vehicle.
// ORDER MATTERS: only append new entries at the end, or both players must update.

public func CP2077Coop_FallbackVehicle() -> TweakDBID {
    return t"Vehicle.v_standard2_archer_hella_player";
}

public func CP2077Coop_VehicleList() -> array<TweakDBID> {
    return [
        t"Vehicle.v_sport1_herrera_outlaw_heist_player",
        t"Vehicle.v_sport1_herrera_outlaw_player",
        t"Vehicle.v_sport1_herrera_riptide_player",
        t"Vehicle.v_sport1_quadra_sport_r7_player",
        t"Vehicle.v_sport1_quadra_turbo_player",
        t"Vehicle.v_sport1_quadra_turbo_r_player",
        t"Vehicle.v_sport1_rayfield_aerondight_player",
        t"Vehicle.v_sport1_rayfield_caliburn_02_player",
        t"Vehicle.v_sport1_rayfield_caliburn_player",
        t"Vehicle.v_sport2_mizutani_shion_base_player",
        t"Vehicle.v_sport2_mizutani_shion_nomad_02_player",
        t"Vehicle.v_sport2_mizutani_shion_nomad_player",
        t"Vehicle.v_sport2_mizutani_shion_player",
        t"Vehicle.v_sport2_porsche_911turbo_cabrio_player",
        t"Vehicle.v_sport2_porsche_911turbo_player",
        t"Vehicle.v_sport2_quadra_type66_02_player",
        t"Vehicle.v_sport2_quadra_type66_avenger_player",
        t"Vehicle.v_sport2_quadra_type66_nomad_ncu_player",
        t"Vehicle.v_sport2_quadra_type66_nomad_player",
        t"Vehicle.v_sport2_quadra_type66_player",
        t"Vehicle.v_sport2_villefort_alvarado_hearse_player",
        t"Vehicle.v_sport2_villefort_alvarado_player",
        t"Vehicle.v_sport2_villefort_alvarado_valentinos_player",
        t"Vehicle.v_sport2_villefort_deleon_player",
        t"Vehicle.v_sportbike1_yaiba_kusanagi_player",
        t"Vehicle.v_sportbike1_yaiba_kusanagi_tyger_player",
        t"Vehicle.v_sportbike2_arch_jackie_player",
        t"Vehicle.v_sportbike2_arch_jackie_tuned_player",
        t"Vehicle.v_sportbike2_arch_linas_player",
        t"Vehicle.v_sportbike2_arch_player",
        t"Vehicle.v_sportbike2_arch_tyger_player",
        t"Vehicle.v_sportbike3_brennan_apollo_nomad_player",
        t"Vehicle.v_sportbike3_brennan_apollo_player",
        t"Vehicle.v_standard2_archer_bandit_player",
        t"Vehicle.v_standard2_archer_hella_player",
        t"Vehicle.v_standard2_archer_quartz_nomad_player",
        t"Vehicle.v_standard2_archer_quartz_player",
        t"Vehicle.v_standard2_chevalier_thrax_player",
        t"Vehicle.v_standard2_makigai_maimai_player",
        t"Vehicle.v_standard2_mizutani_hozuki_player",
        t"Vehicle.v_standard2_thorton_colby_pickup_player",
        t"Vehicle.v_standard2_thorton_colby_player",
        t"Vehicle.v_standard2_thorton_galena_bobas_player",
        t"Vehicle.v_standard2_thorton_galena_gt_player",
        t"Vehicle.v_standard2_thorton_galena_nomad_player",
        t"Vehicle.v_standard2_thorton_galena_player",
        t"Vehicle.v_standard2_villefort_cortes_delamain_player",
        t"Vehicle.v_standard2_villefort_cortes_player",
        t"Vehicle.v_standard25_mahir_supron_player",
        t"Vehicle.v_standard25_thorton_colby_nomad_player",
        t"Vehicle.v_standard25_thorton_colby_pickup_02_player",
        t"Vehicle.v_standard25_thorton_colby_pickup_kurtz_player",
        t"Vehicle.v_standard25_thorton_colby_pickup_player",
        t"Vehicle.v_standard25_thorton_merrimac_player",
        t"Vehicle.v_standard25_villefort_columbus_player",
        t"Vehicle.v_standard3_chevalier_emperor_player",
        t"Vehicle.v_standard3_mahir_supron_kurtz_player",
        t"Vehicle.v_standard3_makigai_tanishi_player",
        t"Vehicle.v_standard3_militech_hellhound_player",
        t"Vehicle.v_standard3_thorton_mackinaw_02_player",
        t"Vehicle.v_standard3_thorton_mackinaw_ncu_player",
        t"Vehicle.v_standard3_thorton_mackinaw_player",
        t"Vehicle.v_utility4_thorton_mackinaw_bmf_player"
    ];
}


// ------------------------------------------------------------
// LOCAL PLAYER
// ------------------------------------------------------------

// -1 = not in a vehicle, 0 = vehicle not in list, 1..N = list index + 1
@addMethod(PlayerPuppet)
public func CP2077Coop_GetMountedVehicleIndex() -> Int32 {
    let vehicle: wref<VehicleObject>;

    if !VehicleComponent.GetVehicle(this.GetGame(), this, vehicle) || !IsDefined(vehicle) {
        return -1;
    }

    return ArrayFindFirst(CP2077Coop_VehicleList(), vehicle.GetRecordID()) + 1;
}

// Pose sent over the network while driving: [x, y, z, forwardX, forwardY] of
// the vehicle origin, empty when on foot. The other side places its copy of
// the car exactly there (the player's seat is offset from the origin).
@addMethod(PlayerPuppet)
public func CP2077Coop_GetMountedVehiclePose() -> array<Float> {
    let vehicle: wref<VehicleObject>;
    let pose: array<Float>;

    if !VehicleComponent.GetVehicle(this.GetGame(), this, vehicle) || !IsDefined(vehicle) {
        return pose;
    }

    let position = vehicle.GetWorldPosition();
    let forward = vehicle.GetWorldForward();

    return [position.X, position.Y, position.Z, forward.X, forward.Y];
}


// ------------------------------------------------------------
// REMOTE VEHICLE
// ------------------------------------------------------------

@addMethod(PlayerPuppet)
private func CP2077Coop_VehicleRecordFor(index: Int32) -> TweakDBID {
    let list = CP2077Coop_VehicleList();

    if index >= 1 && index <= ArraySize(list) {
        return list[index - 1];
    }

    return CP2077Coop_FallbackVehicle();
}

@addMethod(PlayerPuppet)
private func CP2077Coop_GetRemoteVehicle() -> ref<VehicleObject> {
    let system = GameInstance.GetDynamicEntitySystem();

    if !IsDefined(system) || !system.IsReady() {
        return null;
    }

    let entities = system.GetTagged(n"CP2077Coop.RemoteVehicle");

    if ArraySize(entities) == 0 {
        return null;
    }

    return entities[0] as VehicleObject;
}

// Spawns (if needed) and moves the remote player's vehicle.
// Returns true once the vehicle exists and was moved.
@addMethod(PlayerPuppet)
// slope: rise per metre along the heading (forwardX/forwardY is a horizontal
// unit vector), so the car is pitched with the road instead of placed level.
public func CP2077Coop_ShowRemoteVehicle(index: Int32, x: Float, y: Float, z: Float, forwardX: Float, forwardY: Float, slope: Float) -> Bool {
    let system = GameInstance.GetDynamicEntitySystem();

    if !IsDefined(system) || !system.IsReady() {
        return false;
    }

    let recordID = this.CP2077Coop_VehicleRecordFor(index);
    let position = new Vector4(x, y, z, 1.0);
    let rotation = Vector4.ToRotation(new Vector4(forwardX, forwardY, slope, 0.0));
    let vehicle = this.CP2077Coop_GetRemoteVehicle();

    if IsDefined(vehicle) {
        if vehicle.GetRecordID() == recordID {
            GameInstance.GetTeleportationFacility(this.GetGame()).Teleport(vehicle, position, rotation);
            return true;
        }

        // remote player switched cars
        system.DeleteTagged(n"CP2077Coop.RemoteVehicle");
        return false;
    }

    if system.IsPopulated(n"CP2077Coop.RemoteVehicle") {
        // spawn requested, entity not attached yet
        return false;
    }

    let spec = new DynamicEntitySpec();
    spec.recordID = recordID;
    spec.position = position;
    spec.orientation = EulerAngles.ToQuat(rotation);
    spec.persistState = false;
    spec.persistSpawn = false;
    spec.alwaysSpawned = true;
    spec.spawnInView = true;
    spec.active = true;
    spec.tags = [n"CP2077Coop.RemoteVehicle"];

    system.CreateEntity(spec);
    return false;
}

@addMethod(PlayerPuppet)
public func CP2077Coop_HideRemoteVehicle() -> Void {
    let system = GameInstance.GetDynamicEntitySystem();

    if IsDefined(system) && system.IsReady() && system.IsPopulated(n"CP2077Coop.RemoteVehicle") {
        system.DeleteTagged(n"CP2077Coop.RemoteVehicle");
    }
}

// ------------------------------------------------------------
// REMOTE AVATAR WHILE THE REMOTE PLAYER DRIVES
// ------------------------------------------------------------

// Parts hidden by CP2077Coop_SetRemoteAvatarVisible(false), shown again later.
@addField(PlayerPuppet)
private let m_coopHiddenAvatarParts: array<wref<IComponent>>;

// The car stands in for the remote player while they drive, so the avatar
// waits out of sight instead of being pushed into the car's body. Hides only
// the meshes that are on now and shows exactly those again. Needs Codeware.
@if(ModuleExists("Codeware"))
@addMethod(PlayerPuppet)
public func CP2077Coop_SetRemoteAvatarVisible(visible: Bool) -> Bool {
    if visible {
        for part in this.m_coopHiddenAvatarParts {
            if IsDefined(part) {
                part.Toggle(true);
            }
        }

        ArrayClear(this.m_coopHiddenAvatarParts);
        return true;
    }

    let system = GameInstance.GetDynamicEntitySystem();

    if !IsDefined(system) || !system.IsReady() {
        return false;
    }

    let entities = system.GetTagged(n"CP2077Coop.Remote");

    if ArraySize(entities) == 0 {
        return false;
    }

    for component in entities[0].GetComponents() {
        if IsDefined(component as IVisualComponent) && component.IsEnabled() {
            component.Toggle(false);
            ArrayPush(this.m_coopHiddenAvatarParts, component);
        }
    }

    return true;
}
