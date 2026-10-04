// CP2077Coop dev helper: auto-continue into the newest save on game launch.
//
// For fast test iteration. Once per game launch, as soon as the main menu
// knows about your saves, it waits a moment and then does exactly what the
// "Continue" button does. It never fires again in the same session, so
// "Exit to main menu" leaves you in the menu.
//
//   Enable:    keep this file at r6\scripts\CP2077Coop\autoload.reds
//   Disable:   rename it, e.g. to autoload.reds.off (only *.reds files are compiled)
//   Skip once: click any main menu entry before the countdown ends
//
// It also skips the logo videos before the start screen (needs Codeware,
// which CP2077Coop already requires). The "press any key" start screen is
// not skipped here: add -skipStartScreen to the game's launch options.
//
// Log lines go through FTLog, prefixed [CP2077Coop][AutoLoad]. With CET
// installed they show up in the CET "Game Log" window and in
// bin\x64\plugins\cyber_engine_tweaks\gamelog.log.
//
// Game code mirrored (2.31a, cyberpunk/UI/fullscreen/pregame/singleplayerMenu.script):
//   SingleplayerMenuGameController.HandleMenuItemActivate(PauseMenuAction.QuickLoad)
//     m_isModded ? LoadModdedSave(0) : GetSystemRequestsHandler().LoadLastCheckpoint(false)
// m_isModded is filled in by OnSaveMetadataReady(saveIndex 0), so we wait for
// that metadata first: loading a modded save through LoadLastCheckpoint opens
// a dialog instead of loading.


// ------------------------------------------------------------
// SETTINGS
// ------------------------------------------------------------

// Seconds to wait after the save list is ready before continuing.
public func CP2077Coop_AutoLoadDelay() -> Float { return 1.0; }

// Seconds between checks while waiting for the newest save's metadata.
public func CP2077Coop_AutoLoadPollInterval() -> Float { return 0.25; }

// Stop waiting for the metadata after this many checks (32 x 0.25 s = 8 s)
// and continue the way an early click on "Continue" would.
public func CP2077Coop_AutoLoadMaxPolls() -> Int32 { return 32; }

// Load behind the fast travel loading screen. The default loading screen ends
// on a "press a key to continue" prompt; this one drops straight into the game.
// The pause menu's "Exit to main menu" uses the same event.
public func CP2077Coop_AutoLoadUseFastLoadingScreen() -> Bool { return true; }

// Skip the logo videos and intro message before the start screen.
public func CP2077Coop_AutoLoadSkipIntroVideos() -> Bool { return true; }


// ------------------------------------------------------------
// LOGGING AND SESSION STATE
// ------------------------------------------------------------

public func CP2077Coop_AutoLoadLog(message: String) -> Void {
    FTLog("[CP2077Coop][AutoLoad] " + message);
}

// The main menu controller is rebuilt every time the menu opens, so the
// "already done" flag lives on a blackboard definition instead:
// GetAllBlackboardDefs() is created once per process and lives until the
// game exits.
@addField(DebugDataDef)
public let CP2077Coop_AutoLoadDone: Bool;

public func CP2077Coop_AutoLoadIsDone() -> Bool {
    return GetAllBlackboardDefs().DebugData.CP2077Coop_AutoLoadDone;
}

public func CP2077Coop_AutoLoadMarkDone(reason: String) -> Void {
    let debugData: ref<DebugDataDef> = GetAllBlackboardDefs().DebugData;

    if debugData.CP2077Coop_AutoLoadDone {
        return;
    }

    debugData.CP2077Coop_AutoLoadDone = true;
    CP2077Coop_AutoLoadLog("finished for this session: " + reason);
}


// ------------------------------------------------------------
// INTRO LOGO VIDEOS
// ------------------------------------------------------------

// The boot splash screen is an ink loading screen whose native controller
// reads the animation names to play from these fields. "after_skip_pressed"
// is what it plays once the player presses skip, so pointing every stage at
// it skips the logo train and intro message (same approach as djkovrik's
// "No Intro Videos" redscript version). Codeware declares the class. The
// method goes on the vanilla base class: adding it to the Codeware-declared
// class directly fails to compile with the game's scc.
@if(ModuleExists("Codeware"))
@addMethod(ILoadingLogicController)
protected cb func OnInitialize() -> Bool {
    if !CP2077Coop_AutoLoadSkipIntroVideos() || !this.IsA(n"inkSplashScreenLoadingScreenLogicController") {
        return false;
    }

    let splash: ref<SplashScreenLoadingScreenLogicController> = this as SplashScreenLoadingScreenLogicController;
    splash.logosTrainAnimation = n"after_skip_pressed";
    splash.localizedMessageAnimation = n"after_skip_pressed";
    splash.gameIntroAnimation = n"after_skip_pressed";
    splash.longLogosTrainAnimation = n"after_skip_pressed";
    CP2077Coop_AutoLoadLog("intro logo videos skipped");
    return true;
}


// ------------------------------------------------------------
// TIMER
// ------------------------------------------------------------

// Holds only a weak reference: if the menu closes before the timer fires,
// the callback does nothing and the next menu instance arms its own timer.
public class CP2077Coop_AutoLoadCallback extends DelayCallback {
    private let m_menu: wref<SingleplayerMenuGameController>;

    public static func Create(menu: ref<SingleplayerMenuGameController>) -> ref<CP2077Coop_AutoLoadCallback> {
        let callback: ref<CP2077Coop_AutoLoadCallback> = new CP2077Coop_AutoLoadCallback();
        callback.m_menu = menu;
        return callback;
    }

    public func Call() -> Void {
        let menu: ref<SingleplayerMenuGameController> = this.m_menu;

        if IsDefined(menu) {
            menu.CP2077Coop_AutoLoadTick();
        }
    }
}


// ------------------------------------------------------------
// MAIN MENU HOOKS
// ------------------------------------------------------------

@addField(SingleplayerMenuGameController)
private let m_coopAutoLoadArmed: Bool;

@addField(SingleplayerMenuGameController)
private let m_coopAutoLoadMetadataReady: Bool;

@addField(SingleplayerMenuGameController)
private let m_coopAutoLoadPolls: Int32;

@wrapMethod(SingleplayerMenuGameController)
protected cb func OnSavesForLoadReady(saves: array<String>) -> Bool {
    let result: Bool = wrappedMethod(saves);
    this.CP2077Coop_AutoLoadArm();
    return result;
}

@wrapMethod(SingleplayerMenuGameController)
protected cb func OnSaveMetadataReady(info: ref<SaveMetadataInfo>) -> Bool {
    let result: Bool = wrappedMethod(info);

    if IsDefined(info) && info.saveIndex == 0 && !this.m_coopAutoLoadMetadataReady {
        this.m_coopAutoLoadMetadataReady = true;

        if !info.isValid && !CP2077Coop_AutoLoadIsDone() {
            CP2077Coop_AutoLoadMarkDone("newest save is reported invalid, staying in the main menu");
        }
    }

    return result;
}

// Every main menu entry the player activates ends up here, so using the menu
// by hand cancels the auto-continue for the rest of the session. The
// auto-continue itself marks the session done before calling this.
@wrapMethod(SingleplayerMenuGameController)
protected func HandleMenuItemActivate(data: ref<PauseMenuListItemData>) -> Bool {
    if !CP2077Coop_AutoLoadIsDone() {
        CP2077Coop_AutoLoadMarkDone("a main menu entry was used by hand");
    }

    return wrappedMethod(data);
}

@addMethod(SingleplayerMenuGameController)
private func CP2077Coop_AutoLoadArm() -> Void {
    if CP2077Coop_AutoLoadIsDone() || this.m_coopAutoLoadArmed {
        return;
    }

    if this.m_savesCount <= 0 {
        CP2077Coop_AutoLoadLog("no saves found, staying in the main menu");
        return;
    }

    if !this.GetSystemRequestsHandler().IsPreGame() {
        return;
    }

    if IsDefined(this.m_uiSystem) && !this.m_uiSystem.GetIsEulaAccepted() {
        CP2077Coop_AutoLoadLog("EULA not accepted yet, staying in the main menu");
        return;
    }

    this.m_coopAutoLoadArmed = true;
    this.m_coopAutoLoadPolls = 0;

    let delaySystem: ref<DelaySystem> = GameInstance.GetDelaySystem(GetGameInstance());

    if !IsDefined(delaySystem) {
        CP2077Coop_AutoLoadLog("DelaySystem unavailable, continuing right away");
        this.CP2077Coop_AutoLoadFire();
        return;
    }

    CP2077Coop_AutoLoadLog(
        ToString(this.m_savesCount) + " saves found, continuing in "
        + FloatToStringPrec(CP2077Coop_AutoLoadDelay(), 1) + " s (click any menu entry to cancel)"
    );
    delaySystem.DelayCallback(CP2077Coop_AutoLoadCallback.Create(this), CP2077Coop_AutoLoadDelay(), false);
}

// The menu counts as initialized once FinishMenuInitialization has spawned the
// Continue tooltip (its last step); the newest save's metadata tells us
// whether Continue must use the modded-save path.
@addMethod(SingleplayerMenuGameController)
public func CP2077Coop_AutoLoadTick() -> Void {
    if CP2077Coop_AutoLoadIsDone() {
        return;
    }

    let menuReady: Bool = IsDefined(this.m_continueGameTooltipController);
    let metadataReady: Bool = this.m_coopAutoLoadMetadataReady;

    if (!menuReady || !metadataReady) && this.m_coopAutoLoadPolls < CP2077Coop_AutoLoadMaxPolls() {
        let delaySystem: ref<DelaySystem> = GameInstance.GetDelaySystem(GetGameInstance());

        if IsDefined(delaySystem) {
            this.m_coopAutoLoadPolls += 1;
            delaySystem.DelayCallback(CP2077Coop_AutoLoadCallback.Create(this), CP2077Coop_AutoLoadPollInterval(), false);
            return;
        }
    }

    if !menuReady {
        CP2077Coop_AutoLoadLog("main menu did not finish initializing in time, continuing anyway");
    }

    if !metadataReady {
        CP2077Coop_AutoLoadLog("metadata of the newest save did not arrive, continuing without the modded-save check");
    }

    this.CP2077Coop_AutoLoadFire();
}

@addMethod(SingleplayerMenuGameController)
private func CP2077Coop_AutoLoadFire() -> Void {
    let requests: wref<inkISystemRequestsHandler> = this.GetSystemRequestsHandler();

    if !requests.IsPreGame() {
        CP2077Coop_AutoLoadMarkDone("a game is already running");
        return;
    }

    if this.m_savesCount <= 0 {
        CP2077Coop_AutoLoadLog("no saves left to continue, staying in the main menu");
        return;
    }

    let latest: ref<LatestSaveMetadataInfo> = requests.GetLatestSaveMetadata();
    let description: String = "continuing the newest save";

    if IsDefined(latest) {
        description += " (" + latest.locationName + ")";
    }

    // Mark first: the HandleMenuItemActivate wrapper must not treat this call
    // as a manual click, and nothing may fire a second load.
    CP2077Coop_AutoLoadMarkDone(description + ", modded=" + ToString(this.m_isModded));

    if CP2077Coop_AutoLoadUseFastLoadingScreen() {
        let loadingScreenEvent: ref<inkSetNextLoadingScreenEvent> = new inkSetNextLoadingScreenEvent();
        loadingScreenEvent.SetNextLoadingScreenType(inkLoadingScreenType.FastTravel);
        this.QueueBroadcastEvent(loadingScreenEvent);
    }

    let continueItem: ref<PauseMenuListItemData> = new PauseMenuListItemData();
    continueItem.action = PauseMenuAction.QuickLoad;

    if !this.HandleMenuItemActivate(continueItem) {
        CP2077Coop_AutoLoadLog("the main menu refused Continue, staying in the main menu");
    }
}
