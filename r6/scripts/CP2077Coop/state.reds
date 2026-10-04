// CP2077Coop gameplay state sync.
//
// Reads the local player's crouch / weapon / vehicle state and the world
// time and weather, and applies the remote player's state to the avatar NPC.
// Lua (init.lua) packs these values into the network packet.

// Bit flags shared with init.lua
public func CP2077Coop_FlagCrouch() -> Int32 { return 1; }
public func CP2077Coop_FlagWeaponDrawn() -> Int32 { return 2; }
public func CP2077Coop_FlagAiming() -> Int32 { return 4; }
public func CP2077Coop_FlagFiring() -> Int32 { return 8; }
public func CP2077Coop_FlagInVehicle() -> Int32 { return 16; }
// weapon class is stored above the 5 flag bits (multiplier = 2^5)
public func CP2077Coop_WeaponClassMultiplier() -> Int32 { return 32; }

// Weapon classes (3 bits) shared with init.lua
enum CP2077CoopWeaponClass {
    None = 0,
    Pistol = 1,
    Rifle = 2,
    Shotgun = 3,
    Sniper = 4,
    Blade = 5,
    Blunt = 6,
    Other = 7
}


// ------------------------------------------------------------
// REMOTE AVATAR LOOKUP
// ------------------------------------------------------------

@addMethod(PlayerPuppet)
private func CP2077Coop_GetRemotePuppet() -> ref<NPCPuppet> {
    let system = GameInstance.GetDynamicEntitySystem();

    if !IsDefined(system) || !system.IsReady() {
        return null;
    }

    let entities = system.GetTagged(n"CP2077Coop.Remote");

    if ArraySize(entities) == 0 {
        return null;
    }

    return entities[0] as NPCPuppet;
}


// ------------------------------------------------------------
// READ LOCAL PLAYER STATE
// ------------------------------------------------------------

@addMethod(PlayerPuppet)
private func CP2077Coop_ClassifyWeapon(itemID: ItemID) -> CP2077CoopWeaponClass {
    switch RPGManager.GetItemType(itemID) {
        case gamedataItemType.Wea_Handgun:
        case gamedataItemType.Wea_Revolver:
            return CP2077CoopWeaponClass.Pistol;
        case gamedataItemType.Wea_AssaultRifle:
        case gamedataItemType.Wea_Rifle:
        case gamedataItemType.Wea_SubmachineGun:
        case gamedataItemType.Wea_LightMachineGun:
        case gamedataItemType.Wea_HeavyMachineGun:
            return CP2077CoopWeaponClass.Rifle;
        case gamedataItemType.Wea_Shotgun:
        case gamedataItemType.Wea_ShotgunDual:
            return CP2077CoopWeaponClass.Shotgun;
        case gamedataItemType.Wea_SniperRifle:
        case gamedataItemType.Wea_PrecisionRifle:
            return CP2077CoopWeaponClass.Sniper;
        case gamedataItemType.Wea_Katana:
        case gamedataItemType.Wea_Knife:
        case gamedataItemType.Wea_LongBlade:
        case gamedataItemType.Wea_ShortBlade:
        case gamedataItemType.Wea_Machete:
        case gamedataItemType.Wea_Sword:
        case gamedataItemType.Wea_Chainsword:
        case gamedataItemType.Wea_Axe:
        case gamedataItemType.Wea_Melee:
        case gamedataItemType.Cyb_MantisBlades:
            return CP2077CoopWeaponClass.Blade;
        case gamedataItemType.Wea_Hammer:
        case gamedataItemType.Wea_OneHandedClub:
        case gamedataItemType.Wea_TwoHandedClub:
            return CP2077CoopWeaponClass.Blunt;
        // fists and arm cyberware are "active weapons" too, but there is
        // no avatar prop for them: empty hands, not a stand-in pistol
        case gamedataItemType.Wea_Fists:
        case gamedataItemType.Cyb_StrongArms:
        case gamedataItemType.Cyb_NanoWires:
        case gamedataItemType.Cyb_Launcher:
            return CP2077CoopWeaponClass.None;
        default:
            return CP2077CoopWeaponClass.Other;
    }
}

@addMethod(PlayerPuppet)
public func CP2077Coop_GetStateFlags() -> Int32 {
    let flags = 0;
    let stateMachine = this.GetPlayerStateMachineBlackboard();
    let definitions = GetAllBlackboardDefs().PlayerStateMachine;

    if IsDefined(stateMachine) {
        let locomotion = stateMachine.GetInt(definitions.Locomotion);
        if locomotion == EnumInt(gamePSMLocomotionStates.Crouch) {
            flags += CP2077Coop_FlagCrouch();
        }

        let upperBody = stateMachine.GetInt(definitions.UpperBody);
        if upperBody == EnumInt(gamePSMUpperBodyStates.Aim) {
            flags += CP2077Coop_FlagAiming();
        }

        let rangedWeapon = stateMachine.GetInt(definitions.Weapon);
        if rangedWeapon == EnumInt(gamePSMRangedWeaponStates.Shoot) {
            flags += CP2077Coop_FlagFiring();
        }
    }

    let weapon = GameObject.GetActiveWeapon(this);
    if IsDefined(weapon) {
        let weaponClass = this.CP2077Coop_ClassifyWeapon(weapon.GetItemID());
        flags += CP2077Coop_FlagWeaponDrawn();
        flags += EnumInt(weaponClass) * CP2077Coop_WeaponClassMultiplier();
    }

    if VehicleComponent.IsMountedToVehicle(this.GetGame(), this) {
        flags += CP2077Coop_FlagInVehicle();
    }

    return flags;
}

@addMethod(PlayerPuppet)
public func CP2077Coop_GetTimeOfDayMinutes() -> Int32 {
    let gameTime = GameInstance.GetTimeSystem(this.GetGame()).GetGameTime();
    return GameTime.Hours(gameTime) * 60 + GameTime.Minutes(gameTime);
}


// ------------------------------------------------------------
// WORLD STATE (host -> joiner)
// ------------------------------------------------------------

// Moves the clock to the given time of day by the shortest signed step
// (at most 12 h either way). SetGameTimeByHMS works inside the current game
// day, so a correction across midnight (host 00:04, joiner 23:58, or a host
// sleeping 22:00 -> 04:00) moved the joiner's clock by about a whole day and
// fired or postponed a day's worth of GameTime listeners.
@addMethod(PlayerPuppet)
public func CP2077Coop_SetTimeOfDayMinutes(minutes: Int32) -> Void {
    let timeSystem = GameInstance.GetTimeSystem(this.GetGame());
    let now = timeSystem.GetGameTime();
    let current = GameTime.Hours(now) * 60 + GameTime.Minutes(now);
    let target = ((minutes % 1440) + 1440) % 1440;
    let delta = target - current;

    if delta > 720 {
        delta -= 1440;
    } else {
        if delta < -720 {
            delta += 1440;
        }
    }

    timeSystem.SetGameTimeBySeconds(GameTime.GetSeconds(now) - GameTime.Seconds(now) + delta * 60);
}

// Index into this list is what travels over the network.
@addMethod(PlayerPuppet)
private func CP2077Coop_WeatherNames() -> array<CName> {
    return [
        n"24h_weather_sunny",
        n"24h_weather_light_clouds",
        n"24h_weather_cloudy",
        n"24h_weather_heavy_clouds",
        n"24h_weather_fog",
        n"24h_weather_pollution",
        n"24h_weather_toxic_rain",
        n"24h_weather_sandstorm",
        n"24h_weather_rain"
    ];
}

@addMethod(PlayerPuppet)
public func CP2077Coop_GetWeatherIndex() -> Int32 {
    let state = GameInstance.GetWeatherSystem(this.GetGame()).GetWeatherState();

    if !IsDefined(state) {
        return -1;
    }

    return ArrayFindFirst(this.CP2077Coop_WeatherNames(), state.name);
}

// false = not applied (index outside the list, or the game refused, e.g.
// during an area transition or under a higher-priority quest weather).
// The priority-5 override holds until CP2077Coop_ReleaseWeather.
@addMethod(PlayerPuppet)
public func CP2077Coop_SetWeatherIndex(index: Int32) -> Bool {
    let names = this.CP2077Coop_WeatherNames();

    if index < 0 || index >= ArraySize(names) {
        return false;
    }

    return GameInstance.GetWeatherSystem(this.GetGame()).SetWeather(names[index], 10.0, 5u);
}

// Ends the override set by CP2077Coop_SetWeatherIndex: the game's own
// weather cycle takes over again.
@addMethod(PlayerPuppet)
public func CP2077Coop_ReleaseWeather() -> Bool {
    return GameInstance.GetWeatherSystem(this.GetGame()).ResetWeather(true, 10.0);
}


// ------------------------------------------------------------
// APPLY REMOTE PLAYER STATE TO AVATAR
// ------------------------------------------------------------

@addMethod(PlayerPuppet)
public func CP2077Coop_ApplyRemoteStance(crouch: Bool) -> Void {
    let remote = this.CP2077Coop_GetRemotePuppet();

    if !IsDefined(remote) {
        return;
    }

    if crouch {
        NPCPuppet.ChangeStanceState(remote, gamedataNPCStanceState.Crouch);
    } else {
        NPCPuppet.ChangeStanceState(remote, gamedataNPCStanceState.Stand);
    }
}

@addMethod(PlayerPuppet)
private func CP2077Coop_WeaponRecordFor(weaponClass: Int32) -> TweakDBID {
    switch weaponClass {
        case EnumInt(CP2077CoopWeaponClass.Pistol):
            return t"Items.Preset_Lexington_Default";
        case EnumInt(CP2077CoopWeaponClass.Rifle):
            return t"Items.Preset_Ajax_Default";
        case EnumInt(CP2077CoopWeaponClass.Shotgun):
            return t"Items.Preset_Carnage_Default";
        case EnumInt(CP2077CoopWeaponClass.Sniper):
            return t"Items.Preset_Grad_Default";
        case EnumInt(CP2077CoopWeaponClass.Blade):
            return t"Items.Preset_Katana_Default";
        case EnumInt(CP2077CoopWeaponClass.Blunt):
            return t"Items.Preset_Baseball_Bat_Default";
        default:
            return t"Items.Preset_Lexington_Default";
    }
}

@addMethod(PlayerPuppet)
public func CP2077Coop_ApplyRemoteWeapon(weaponClass: Int32, drawn: Bool) -> Void {
    let remote = this.CP2077Coop_GetRemotePuppet();

    if !IsDefined(remote) {
        return;
    }

    let controller = remote.GetAIControllerComponent();

    if !IsDefined(controller) {
        return;
    }

    // None (fists, arm cyberware) and Other (unknown item type) have no
    // matching avatar prop: empty hands instead of the default pistol
    let hasProp = weaponClass != EnumInt(CP2077CoopWeaponClass.None)
        && weaponClass != EnumInt(CP2077CoopWeaponClass.Other);

    if !drawn || !hasProp {
        let holster = new AIUnequipCommand();
        holster.slotId = t"AttachmentSlots.WeaponRight";
        controller.SendCommand(holster);
        return;
    }

    let itemID = ItemID.FromTDBID(this.CP2077Coop_WeaponRecordFor(weaponClass));
    let transactions = GameInstance.GetTransactionSystem(this.GetGame());

    if !transactions.HasItem(remote, itemID) {
        transactions.GiveItem(remote, itemID, 1);
    }

    let equip = new AIEquipCommand();
    equip.slotId = t"AttachmentSlots.WeaponRight";
    equip.itemId = ItemID.GetTDBID(itemID);
    controller.SendCommand(equip);
}
