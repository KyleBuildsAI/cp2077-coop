------------------------------------------------------------
-- CP2077 COOP
--
-- v0.0.32 WORLD + STATE + VEHICLE + COMBAT SYNC + DIAGNOSTICS + TEST BOT
--
-- ROLE: przycisk w panelu 'CP2077 Coop' (zapis do role.txt),
-- albo domyślnie poniżej. role.txt ma pierwszeństwo.
-- HOST:   local IS_HOST = true
-- JOINER: local IS_HOST = false
--
-- ABSOLUTE WORLD COORDINATES
-- JOINER TELEPORT TO HOST
-- REMOTE AVATAR
-- WALK / SPRINT / IDLE
-- SIMPLE CLIENT-SIDE PREDICTION
------------------------------------------------------------


------------------------------------------------------------
-- CONFIG
------------------------------------------------------------

-- HOST uses true.
-- JOINER uses false.
local IS_HOST = true

-- 30 pakietów/s (było 20). OBAJ gracze muszą mieć tę samą
-- wartość - prędkość liczona jest z numeru sekwencji: nadawca
-- wysyła pozycję z równej siatki 30 Hz przy każdym fps
-- (Sync.sendLocalState), więc jeden numer = SEND_INTERVAL.
local SEND_INTERVAL = 1.0 / 30.0

-- Jak często aktualizujemy cel AI.
-- Częstsze anulowanie AIMoveTo powoduje szarpanie (symulacja: 0.12 lepsze niż 0.08).
local COMMAND_INTERVAL = 0.12

-- Predykcja ruchu remote w sekundach (kompensuje ping ~95 ms + reakcję AI).
local PREDICTION_TIME = 0.30
local MAX_PREDICTION_DISTANCE = 2.0

-- Jeżeli avatar odjedzie za daleko, robimy hard correction.
local TELEPORT_DISTANCE = 6.0

local MIN_TARGET_CHANGE = 0.04

-- po zatrzymaniu: korekta, jeśli avatar stoi dalej niż tyle
local IDLE_SETTLE_DISTANCE = 0.35
local IDLE_SETTLE_INTERVAL = 0.50
local MOVEMENT_EPSILON = 0.02
local IDLE_DELAY = 0.25
local RUN_SPEED = 2.6
local SPRINT_SPEED = 5.5

-- Unik / dash / skok / pojazd: AI nie umie tego odtworzyć
-- (navmesh, brak skoku), więc podążamy teleportem.
local FAST_SPEED = 8.0
local VERTICAL_SNAP = 0.8

-- UDP: duży spadek sekwencji = restart klienta drugiego gracza
local SEQUENCE_RESET_GAP = 200

-- po przycięciu gry: najwyżej tyle zaległych tyknięć 30 Hz w jednej
-- klatce (1 s). Push jest tani, DLL wysyła i tak tylko ostatni,
-- a odbiorca widzi lukę w numerach = upływ czasu, nie skok prędkości.
local MAX_SEND_BACKLOG = 30

local ROTATE_INTERVAL = 0.10

-- P2 pojawia się z boku hosta, nie w środku jego postaci.
-- Czasy i próby teleportu do hosta: Sync.JOIN_* (Sync.updateJoin).
local JOIN_OFFSET = 1.75

-- Po przejęciu Judy również wymuszamy jej właściwy world position.
local SPAWN_SNAP_INTERVAL = 0.10
local SPAWN_SNAP_DURATION = 1.50
local SPAWN_SNAP_TOLERANCE = 0.75


------------------------------------------------------------
-- TIMERS
------------------------------------------------------------

-- stan skryptu w jednej tabeli: LuaJIT pozwala max 60 upvalues na funkcję
local S = {}

S.sendAccumulator = 0.0
S.commandAccumulator = 0.0
S.rotateAccumulator = 0.0
S.idleTimer = 0.0

S.spawnSnapPending = false
S.spawnSnapElapsed = 0.0
S.spawnSnapAccumulator = 0.0
-- snap nowego avatara (true) albo po wyjściu z auta (false, NPC już działa)
S.spawnSnapFresh = false
-- s czekania, aż gra ustawi nowy avatar (pozycja jeszcze 0, 0, 0)
S.spawnPlaceWait = 0.0
-- do tej chwili (Sync.clock) teleport avatara to ustawienie po spawnie
-- albo po wyjściu z auta, nie korekta (hard_per_min)
S.spawnGraceUntil = -100.0

-- teleport joinera do hosta (Sync.updateJoin). Faza:
--   "settle"   czekamy, aż gracz stoi w grze Sync.JOIN_SETTLE_SECONDS,
--   "teleport" wysłany JEDEN teleport, czekamy, aż gra go wykona,
--   "retry"    próba nieudana, przerwa przed kolejną,
--   "done" / "gave_up" koniec (S.worldJoinComplete = true).
S.joinPhase = "settle"
S.joinAttempts = 0
-- s w fazie "retry" (przerwa S.joinRetryDelay); Sync.clock przy teleporcie
S.joinPhaseTime = 0.0
S.joinRetryDelay = 0.0
S.joinCalledAt = 0.0
-- s stabilnej gry bez przerwy (bez auta, sceny, skoku pozycji)
S.joinSettled = 0.0
-- s od "sync ON" (wczytanie gry) - do logu
S.joinSinceLoad = 0.0
-- czemu licznik stoi: "in a vehicle" / "in a scene" (nil = nic)
S.joinWaitReason = nil
-- pozycja gracza z poprzedniej klatki (skok = gra jeszcze go ustawia)
S.joinLastX = nil
S.joinLastY = nil
S.joinLastZ = nil
-- pozycja gracza przed teleportem tej próby
S.joinStartX = 0.0
S.joinStartY = 0.0
S.joinStartZ = 0.0
-- wynik ostatniej próby (panel): błąd do punktu teleportu i opis porażki
S.joinError = nil
S.joinFailure = nil
-- s tej próby spędzone w menu (świat stał, teleport nie mógł się wykonać)
S.joinPausedFor = 0.0
-- host też może się jeszcze wczytywać albo właśnie szybko podróżować:
-- s czasu jego pakietów bez skoku pozycji i Sync.clock przy ostatnim skoku
S.hostSettled = 0.0
S.hostJumpAt = -100.0


------------------------------------------------------------
-- SESSION / NETWORK
------------------------------------------------------------

S.syncActive = false

S.rolePrinted = false
S.lastRemoteSequence = -1

-- DLL nigdy nie czyści ostatniego pakietu: numer, który leżał w slocie
-- przy resecie, ignorujemy, aż przyjdzie inny (nil = brak)
S.staleSequence = nil

-- kolejne starsze numery z rzędu (restart drugiej gry bez długiej ciszy)
S.olderStreak = 0
S.lastIgnoredSequence = nil

-- numer i licznik tyknięć ostatniego pakietu RUCHU; pakiety bojowe
-- zużywają numery, ale nie są upływem czasu
S.lastMoveSequence = nil
S.combatSinceMove = 0
S.moveTicks = 0

-- nadawca: pozycja z poprzedniej klatki (interpolacja do siatki 30 Hz)
S.sendPrevX = nil
S.sendPrevY = nil
S.sendPrevZ = nil
S.sendPrevSource = nil


------------------------------------------------------------
-- REMOTE AVATAR
------------------------------------------------------------

S.remoteInitialized = false
S.remoteHandle = nil

-- spawn avatara (Sync.requestSpawn): kiedy ostatnio wołany (nil = nie
-- był), ile żądań, czy zalogowano odłożenie, licznik odpytywania GetTagged
S.spawnRequestedAt = nil
S.spawnAttempts = 0
S.spawnDeferredLogged = false
S.handlePollAccumulator = 0.0

S.activeMoveCommand = nil

S.remoteMoving = false
S.movementType = "Walk"

S.remoteForwardX = 0.0
S.remoteForwardY = 1.0

S.lastFacingX = nil
S.lastFacingY = nil

-- gdzie avatar stał przy ostatnim obrocie w bezruchu
S.facingAtX = 0.0
S.facingAtY = 0.0

-- błąd w poziomie przy poprzednim sprawdzeniu obrotu (czy avatar
-- jeszcze dochodzi); duża wartość = nowe AIMoveTo, zmierzyć od nowa
S.rotateLastError = 999.0

-- drugi gracz jedzie pokazanym autem: avatar ukryty, nie podąża
S.avatarParked = false
S.avatarUnhidePending = false


------------------------------------------------------------
-- RAW NETWORK POSITION / VELOCITY
------------------------------------------------------------

S.previousRemoteX = nil
S.previousRemoteY = nil
S.previousRemoteZ = nil

S.remoteVelocityX = 0.0
S.remoteVelocityY = 0.0
S.remoteVelocityZ = 0.0

S.remoteSpeed = 0.0
S.remoteVerticalJump = false

S.settleAccumulator = 0.50

-- dojście po zatrzymaniu (Steer.settleStep): cel, najmniejszy
-- dotąd błąd, próby bez postępu, czy był już teleport, koniec
S.settleTargetX = nil
S.settleTargetY = nil
S.settleTargetZ = nil
S.settleBest = nil
S.settleStalls = 0
S.settleTeleported = false
S.settleDone = false


------------------------------------------------------------
-- ABSOLUTE WORLD TARGET
------------------------------------------------------------

S.targetX = 0.0
S.targetY = 0.0
S.targetZ = 0.0

S.lastCommandX = nil
S.lastCommandY = nil
S.lastCommandZ = nil


------------------------------------------------------------
-- JOINER WORLD SYNC
------------------------------------------------------------

S.worldJoinComplete = false

S.joinTargetX = 0.0
S.joinTargetY = 0.0
S.joinTargetZ = 0.0

S.joinForwardX = nil
S.joinForwardY = nil


------------------------------------------------------------
-- LOG
--
-- Diag stoi tutaj, przed resztą kodu, żeby każda funkcja mogła
-- logować przez Diag.log (funkcje panelu i statystyk: sekcja
-- DIAGNOSTICS niżej).
------------------------------------------------------------

-- VERSION: podbić przy każdym wdrożeniu (Total Sync Plan). Widać ją w
-- tytule i pierwszym wierszu panelu oraz w każdej linii [STATS]
-- (version=), więc stary build na stanowisku testowym od razu widać.
local Diag = {
    VERSION = "0.0.32",

    STATS_INTERVAL = 5.0,
    MONITOR_READ_INTERVAL = 2.0,
    -- monitor_status.txt zostaje na dysku po zamknięciu monitora: starszy
    -- wpis "updated" = monitor nie działa (pisze co 5 s), stary ping nie
    -- jest już pokazywany jako aktualny
    MONITOR_STALE_AFTER = 30.0,

    -- progi ocen w panelu: { ostrzeżenie od, źle od }. Te same liczby co
    -- EXPECT w coop-tools/coop_monitor.py - zmieniać razem. RTT gracz ->
    -- serwer -> gracz na trasie LA - Warszawa - Rosja to normalnie
    -- ~250-400 ms (sieć + czekanie na klatkę i slot wysyłki). Gracze
    -- i serwer w USA: ~30-80 ms; progi są ustawione pod długą trasę.
    LIMITS = {
        rtt_ms = { 450, 700 },
        server_ping_ms = { 250, 400 },
        missed_pct = { 10, 25 },
        age_ms = { 300, 1500 },
        avatar_err_m = { 2.0, 5.0 }
    },

    -- progi stanu połączenia (sekundy bez nowego pakietu)
    STALE_AFTER = 1.5,
    LOST_AFTER = 5.0,

    -- restart drugiej gry (numery od 1): starszy numer po ciszy
    -- >= STALE_AFTER albo tyle różnych starszych numerów z rzędu
    -- (spóźniony pakiet UDP przychodzi pojedynczo)
    RESTART_OLDER_STREAK = 10,

    -- odczyt slotu DLL przerwany nowym pakietem (pominięty, patrz onUpdate)
    tornReads = 0,

    ROLE_FILE = "role.txt",
    MONITOR_FILE = "monitor_status.txt",

    -- print() CET trafia do bin/x64/plugins/cyber_engine_tweaks/scripting.log:
    -- wspólny dla wszystkich modów i buforowany (linie lądują na dysku
    -- z opóźnieniem). Dla coop_monitor.py piszemy osobno, z zamknięciem
    -- pliku od razu: ostatnia linia [STATS] -> coop_stats_<rola>.txt,
    -- wszystkie linie [CP2077Coop] -> coop_events.log (najwyżej
    -- EVENTS_MAX_LINES, potem zostaje EVENTS_KEEP_LINES ostatnich).
    STATS_FILE_PREFIX = "coop_stats_",
    EVENTS_FILE = "coop_events.log",
    EVENTS_MAX_LINES = 400,
    EVENTS_KEEP_LINES = 200,
    eventLines = {},
    eventsStarted = false,
    -- pliki, których zapis się nie udał (ostrzeżenie raz na plik)
    writeFailed = {},

    visible = true,

    -- Luka w numerach pakietów drugiej strony to jedno z trzech:
    --   overwritten: przyszły w jednej długiej klatce, DLL trzyma tylko
    --     ostatni (lokalny fps < tempo nadawcy, nie sieć),
    --   missed: nie doszły (zgubione w sieci albo pominięte przez DLL
    --     nadawcy: kilka tyknięć w jednej jego klatce = jeden pakiet;
    --     też dwa pakiety ściśnięte jitterem w jedną naszą klatkę).
    -- Liczniki sesji (od Diag.resetSession) i okna STATS_INTERVAL (stat*).
    packetsReceived = 0,
    packetsSent = 0,
    missed = 0,
    overwritten = 0,
    ignored = 0,

    statReceived = 0,
    statMissed = 0,
    statOverwritten = 0,
    -- nadawca: tyknięcia (numery) i ile z nich DLL scaliło (kilka w klatce)
    statPushes = 0,
    statMerged = 0,
    missedPctLast = nil,
    overwrittenPctLast = nil,
    mergedPctLast = nil,

    -- okno 1 s: odczytane pakiety, wysłane tyknięcia, klatki i przyrost
    -- numeru drugiej strony (= jej tempo wysyłki, ~30/s)
    windowReceived = 0,
    windowSent = 0,
    windowFrames = 0,
    windowSeqAdvance = 0,
    windowTimer = 0.0,
    ppsIn = 0.0,
    ppsOut = 0.0,
    fps = 0.0,
    peerRate = 0.0,

    -- czas między dwoma ostatnimi odczytami slotu DLL (Diag.onPoll)
    lastPollClock = nil,
    pollGap = 0.0,

    lastPacketClock = nil,
    -- stan i wiek pakietu z ostatniego odczytu DLL (Diag.updateConnectionState);
    -- start moda = menu główne
    lastState = "PAUSED",
    lastAge = nil,

    statsTimer = 0.0,
    monitorTimer = 0.0,
    monitor = {},

    avatarError = nil,

    -- Okno STATS_INTERVAL: czasy klatek (p99), pakiety z flagami drugiej
    -- strony (na sekundę) i twarde korekty avatara: teleport przy błędzie
    -- >= TELEPORT_DISTANCE i przy dojściu w bezruchu, bez podążania
    -- teleportem przy szybkim ruchu i bez snapu po spawnie. Korekty na
    -- minutę liczone z ostatnich HARD_WINDOWS okien (minuta).
    frameTimes = {},
    frameP99Last = nil,
    statFlagsIn = 0,
    flagsInLast = nil,
    HARD_WINDOWS = 12,
    hardWindow = 0,
    hardTotal = 0,
    hardHistory = {},
    hardPerMinuteLast = nil,

    -- dryf avatara w oknie STATS_INTERVAL (średnia i maksimum)
    driftSum = 0.0,
    driftCount = 0,
    driftMax = 0.0,
    driftAvgLast = nil,
    driftMaxLast = nil,

    teleportRequested = false,
    roleChanged = false,
    botToggleRequested = false,
    testAreaRequested = false,

    -- pusty, płaski teren do testów (AMM: "The Oil Fields", Badlands)
    TEST_AREA = { name = "Oil Fields (Badlands)", x = -1818.82, y = 3858.03, z = 7.16 }
}


-- Zapis całego tekstu i zamknięcie pliku od razu (bez bufora CET).
-- Nieudany zapis: jedno ostrzeżenie na plik, potem cicho dalej.
function Diag.writeFile(path, mode, text)

    local file, err =
        io.open(path, mode)

    if file == nil then

        if not Diag.writeFailed[path] then

            Diag.writeFailed[path] = true
            print("[CP2077Coop] could not write " .. path .. ": " .. tostring(err))
        end

        return false
    end

    file:write(text)
    file:close()

    return true
end


-- Każda linia logu moda: konsola CET (scripting.log) + coop_events.log.
function Diag.log(message)

    print(message)

    local line =
        os.date("%H:%M:%S ") .. message

    local lines = Diag.eventLines
    lines[#lines + 1] = line

    local mode = "a"
    local text = line .. "\n"

    -- pierwsza linia sesji gry: plik od nowa
    if not Diag.eventsStarted then

        Diag.eventsStarted = true
        mode = "w"
    end

    if #lines > Diag.EVENTS_MAX_LINES then

        local kept = {}

        for index = #lines - Diag.EVENTS_KEEP_LINES + 1, #lines do
            kept[#kept + 1] = lines[index]
        end

        Diag.eventLines = kept
        mode = "w"
        text = table.concat(kept, "\n") .. "\n"
    end

    Diag.writeFile(Diag.EVENTS_FILE, mode, text)
end


------------------------------------------------------------
-- MATH
------------------------------------------------------------

local function distance3(
    ax, ay, az,
    bx, by, bz
)
    local dx = ax - bx
    local dy = ay - by
    local dz = az - bz

    return math.sqrt(
        dx * dx +
        dy * dy +
        dz * dz
    )
end


local function distance2(
    ax, ay,
    bx, by
)
    local dx = ax - bx
    local dy = ay - by

    return math.sqrt(
        dx * dx +
        dy * dy
    )
end


------------------------------------------------------------
-- GAME STATE
------------------------------------------------------------

local function isGameplayLoaded()

    local player = Game.GetPlayer()

    if player == nil then
        return false
    end

    if not player:IsAttached() then
        return false
    end

    local requests =
        Game.GetSystemRequestsHandler()

    if requests ~= nil
        and requests:IsPreGame()
    then
        return false
    end

    return true
end


------------------------------------------------------------
-- REMOTE ENTITY
------------------------------------------------------------

local function getRemoteHandle()

    local system =
        Game.GetDynamicEntitySystem()

    if system == nil then
        return nil
    end

    local entities =
        system:GetTagged(
            CName.new(
                "CP2077Coop.Remote"
            )
        )

    if entities == nil or #entities == 0 then
        return nil
    end

    return entities[1]
end


------------------------------------------------------------
-- CANCEL AI MOVE
------------------------------------------------------------

local function cancelMoveCommand()

    if S.remoteHandle == nil then
        S.activeMoveCommand = nil
        return
    end

    if S.activeMoveCommand == nil then
        return
    end

    local controller =
        S.remoteHandle:
            GetAIControllerComponent()

    if controller == nil then
        S.activeMoveCommand = nil
        return
    end

    pcall(function()

        controller:
            StopExecutingCommand(
                S.activeMoveCommand,
                true
            )

    end)

    pcall(function()

        controller:
            CancelCommand(
                S.activeMoveCommand
            )

    end)

    S.activeMoveCommand = nil
end


------------------------------------------------------------
-- REMOTE AI MOVE
------------------------------------------------------------

local function moveRemoteAI(
    x,
    y,
    z,
    moveType,
    crouched
)

    if S.remoteHandle == nil then
        return false
    end

    local controller =
        S.remoteHandle:
            GetAIControllerComponent()

    if controller == nil then
        return false
    end


    local destination =
        NewObject(
            "WorldPosition"
        )

    destination:SetVector4(
        destination,
        Vector4.new(
            x,
            y,
            z,
            1.0
        )
    )


    local positionSpec =
        NewObject(
            "AIPositionSpec"
        )

    positionSpec:SetWorldPosition(
        positionSpec,
        destination
    )


    local command =
        NewObject(
            "handle:AIMoveToCommand"
        )

    command.movementTarget =
        positionSpec

    command.rotateEntityTowardsFacingTarget =
        false

    command.ignoreNavigation =
        false

    command.desiredDistanceFromTarget =
        0.05

    command.movementType =
        moveType or "Walk"

    command.finishWhenDestinationReached =
        true

    -- kucający gracz = avatar porusza się w przysiadzie
    command.alwaysUseStealth =
        crouched == true


    cancelMoveCommand()

    controller:
        SendCommand(command)

    S.activeMoveCommand = command

    -- AIMoveTo obraca NPC w stronę drogi (cofanie, krok w bok,
    -- dojście po zatrzymaniu): następny obrót w bezruchu musi
    -- ustawić avatar od nowa, nawet gdy gracz się nie obrócił
    S.lastFacingX = nil
    S.lastFacingY = nil
    S.rotateLastError = 999.0

    return true
end


------------------------------------------------------------
-- ROTATE REMOTE WHILE IDLE
------------------------------------------------------------

local function rotateRemote(
    forwardX,
    forwardY
)

    if S.remoteHandle == nil then
        return
    end

    local controller =
        S.remoteHandle:
            GetAIControllerComponent()

    if controller == nil then
        return
    end

    local current =
        S.remoteHandle:
            GetWorldPosition()


    local facingPoint =
        Vector4.new(
            current.x + forwardX * 5.0,
            current.y + forwardY * 5.0,
            current.z,
            1.0
        )


    local destination =
        NewObject(
            "WorldPosition"
        )

    destination:SetVector4(
        destination,
        facingPoint
    )


    local positionSpec =
        NewObject(
            "AIPositionSpec"
        )

    positionSpec:SetWorldPosition(
        positionSpec,
        destination
    )


    local command =
        NewObject(
            "handle:AIRotateToCommand"
        )

    command.target =
        positionSpec

    command.angleOffset = 0
    command.angleTolerance = 3
    command.speed = 2

    controller:
        SendCommand(command)
end


------------------------------------------------------------
-- MOD COMPARISON
--
-- modlist.txt (pisany przez coop-tools/devkit.py i coop_monitor.py)
-- zawiera zainstalowane mody: "kategoria/nazwa" w każdej linii.
-- Każda nazwa -> 16-bitowy odcisk. Odciski krążą w pętli przez
-- kanał stanu (typ 6 = starszy bajt, typ 7 = młodszy bajt).
-- Panel pokazuje wspólne mody, mody tylko u nas (z nazwami)
-- i liczbę modów tylko u partnera (nazwy nie mieszczą się w kanale).
--
-- Harmonogram (Mods.due, wołane z Sync.buildPayload): seria (każdy
-- wolny slot dla danych innych niż flagi) aż do "mods compared" plus
-- BURST_CYCLES pełnych cykli, potem jedna para HI+LO co
-- TRICKLE_INTERVAL s. Wcześniej odciski brały 25% pakietów na zawsze.
-- Seria od nowa (Mods.arm), gdy druga strona zaczyna swoją serię po
-- ciszy: po wczytaniu gry, restarcie albo zmianie roli zaczyna od
-- zera i potrzebuje naszej listy. Nasz reset (Mods.resetRemote) też
-- startuje serię - po niej druga strona wysyła nam swoją.
------------------------------------------------------------

local Mods = {
    FILE = "modlist.txt",
    TYPE_HI = 6,
    TYPE_LO = 7,
    STRIDE = 512,
    START_FLAG = 256,
    END_FLAG = 256,
    HASH_MODULO = 65521,
    -- odcisk niemożliwy dla nazw (>= HASH_MODULO): pusta lista
    EMPTY_SENTINEL = 65535,

    -- pełne cykle serii po porównaniu (i po każdym Mods.arm)
    BURST_CYCLES = 2,
    -- po serii: jedna para co tyle sekund (lista krąży dalej powoli)
    TRICKLE_INTERVAL = 10.0,
    -- odstęp między HI drugiej strony: >= QUIET_GAP = cisza / pary co
    -- TRICKLE_INTERVAL, mniej po ciszy = zaczęła serię od nowa
    QUIET_GAP = 5.0,

    names = {},
    hashes = {},
    localSet = {},

    sendIndex = 1,
    sendLow = false,
    -- pełne cykle do wysłania w serii (start gry = seria)
    burstCyclesLeft = 2,
    nextTrickleAt = 0.0,
    -- wysłane pełne cykle (statystyka, testy)
    cyclesSent = 0,

    pendingHigh = nil,
    building = {},
    lastCycle = nil,
    previousCycle = nil,
    cyclesReceived = 0,
    compared = false,
    -- druga strona wysyła odciski (v0.0.30 i starsze: nie)
    peerSendsMods = false,
    lastPeerHighClock = nil,
    peerWasQuiet = true
}


function Mods.hash(name)

    local value = 0
    local lower = string.lower(name)

    for index = 1, #lower do

        value =
            (value * 31 + string.byte(lower, index)) %
            Mods.HASH_MODULO
    end

    return value
end


function Mods.load()

    Mods.names = {}
    Mods.hashes = {}
    Mods.localSet = {}

    local file =
        io.open(Mods.FILE, "r")

    if file == nil then

        Diag.log("[CP2077Coop] no modlist.txt - run coop-tools/devkit.py modlist or coop_monitor.py")
        return
    end

    for line in file:lines() do

        local name =
            string.match(line, "^%s*(.-)%s*$")

        if name ~= nil and name ~= "" then

            local hash = Mods.hash(name)

            if Mods.localSet[hash] == nil then

                Mods.localSet[hash] = name
                Mods.hashes[#Mods.hashes + 1] = hash
            end

            Mods.names[#Mods.names + 1] = name
        end
    end

    file:close()

    Diag.log(string.format("[CP2077Coop] mod list loaded: %d mods", #Mods.names))
end


-- Seria: co najmniej BURST_CYCLES pełnych cykli od teraz. Przerwany
-- cykl się nie liczy (druga strona zbiera listę od flagi START), więc +1.
function Mods.arm()

    local midCycle =
        Mods.sendLow
        or Mods.sendIndex ~= 1

    Mods.burstCyclesLeft =
        math.max(
            Mods.burstCyclesLeft,
            Mods.BURST_CYCLES + (midCycle and 1 or 0)
        )
end


-- Seria trwa, dopóki zostały cykle albo nie mamy jeszcze listy drugiej
-- strony, która swoją wysyła (starsza wersja bez odcisków: tylko cykle).
function Mods.bursting()

    return
        Mods.burstCyclesLeft > 0
        or (
            not Mods.compared
            and Mods.peerSendsMods
        )
end


-- Czy ten slot (wolny od pong/ping/godziny/pogody/pojazdu) niesie
-- odcisk. clock = Sync.clock (Sync jest niżej w pliku).
function Mods.due(clock)

    -- druga połowa pary: LO zaraz po HI
    if Mods.sendLow then
        return true
    end

    if Mods.bursting() then

        -- pierwsza para po serii dopiero za TRICKLE_INTERVAL
        Mods.nextTrickleAt =
            clock +
            Mods.TRICKLE_INTERVAL

        return true
    end

    if clock >= Mods.nextTrickleAt then

        Mods.nextTrickleAt =
            clock +
            Mods.TRICKLE_INTERVAL

        return true
    end

    return false
end


-- Następny pakiet z odciskiem (lista krąży w pętli; kiedy - Mods.due).
function Mods.nextPayload()

    local count = #Mods.hashes

    local hash =
        count > 0 and Mods.hashes[Mods.sendIndex] or Mods.EMPTY_SENTINEL

    if not Mods.sendLow then

        Mods.sendLow = true

        local start =
            (Mods.sendIndex == 1) and Mods.START_FLAG or 0

        return
            Mods.TYPE_HI * Mods.STRIDE +
            math.floor(hash / 256) +
            start
    end

    Mods.sendLow = false

    local isLast =
        count == 0 or Mods.sendIndex >= count

    local finish =
        isLast and Mods.END_FLAG or 0

    if isLast then

        Mods.sendIndex = 1
        Mods.cyclesSent = Mods.cyclesSent + 1

        if Mods.burstCyclesLeft > 0 then
            Mods.burstCyclesLeft = Mods.burstCyclesLeft - 1
        end
    else
        Mods.sendIndex = Mods.sendIndex + 1
    end

    return
        Mods.TYPE_LO * Mods.STRIDE +
        hash % 256 +
        finish
end


-- Druga strona zaczęła serię po ciszy: wysyłamy naszą listę od nowa.
-- Liczą się tylko HI (para HI+LO z serii po ciszy wyglądałaby jak
-- seria). Seria w toku nie jest "nowa", a nasza seria wywołana jej
-- startem idzie bez przerwy: druga strona nie widzi nowego startu,
-- więc nie ma ping-ponga.
function Mods.notePeerHigh(clock)

    local gap = math.huge

    if Mods.lastPeerHighClock ~= nil then
        gap = clock - Mods.lastPeerHighClock
    end

    if gap < Mods.QUIET_GAP
        and Mods.peerWasQuiet
    then
        Mods.arm()
    end

    Mods.peerWasQuiet =
        gap >= Mods.QUIET_GAP

    Mods.lastPeerHighClock = clock
end


-- clock = Sync.clock (Sync jest niżej w pliku).
function Mods.receive(packetType, value, clock)

    local flag =
        value >= 256

    local byte =
        value % 256

    Mods.peerSendsMods = true

    if packetType == Mods.TYPE_HI then

        Mods.notePeerHigh(clock)

        -- start nowego cyklu, a poprzedni nie dostał znacznika końca
        -- (zgubiona albo nadpisana ostatnia para): liczy się i tak,
        -- zamiast czekać na pełny cykl z par co TRICKLE_INTERVAL
        if flag then

            if next(Mods.building) ~= nil then
                Mods.closeCycle(false)
            end

            Mods.building = {}
        end

        Mods.pendingHigh = byte
        return
    end

    -- TYPE_LO bez poprzedzającego HI (zgubiony albo nadpisany w DLL):
    -- bez odcisku, ale znacznik końca cyklu się liczy. Inaczej jeden
    -- zgubiony HI ostatniej pary = cykl przepada, a następny pełny
    -- przychodzi dopiero z par co TRICKLE_INTERVAL (minuta i więcej).
    -- Pusty zbiór (np. koniec cyklu sprzed naszego resetu) nic nie mówi.
    local complete =
        Mods.pendingHigh ~= nil

    if Mods.pendingHigh ~= nil then

        local hash =
            Mods.pendingHigh * 256 +
            byte

        Mods.pendingHigh = nil

        if hash ~= Mods.EMPTY_SENTINEL then
            Mods.building[hash] = true
        end

    elseif next(Mods.building) == nil then
        return
    end

    if flag then
        Mods.closeCycle(complete)
    end
end


-- Koniec cyklu odcisków drugiej strony: dwa ostatnie cykle tworzą jej
-- listę (Mods.remoteSet), po dwóch pierwszych porównanie. Cykl
-- uszkodzony (bez ostatniej pary albo bez znacznika końca) liczy się do
-- porównania, ale jego odciski dołączają do najnowszego cyklu, zamiast
-- wypychać pełny cykl z sumy.
function Mods.closeCycle(complete)

    if complete or Mods.lastCycle == nil then

        Mods.previousCycle = Mods.lastCycle
        Mods.lastCycle = Mods.building

    else

        for hash in pairs(Mods.building) do
            Mods.lastCycle[hash] = true
        end
    end

    Mods.building = {}
    Mods.cyclesReceived = Mods.cyclesReceived + 1

    if not Mods.compared and Mods.cyclesReceived >= 2 then

        Mods.compared = true
        Mods.logComparison()

        -- "mods compared" + BURST_CYCLES pełnych cykli: druga strona
        -- też dostaje naszą listę dwa razy w całości
        Mods.arm()
    end
end


-- Suma dwóch ostatnich pełnych cykli: zgubiony pakiet nie robi
-- fałszywego "partner nie ma tego moda".
function Mods.remoteSet()

    if Mods.lastCycle == nil then
        return nil
    end

    local union = {}

    for hash in pairs(Mods.lastCycle) do
        union[hash] = true
    end

    if Mods.previousCycle ~= nil then

        for hash in pairs(Mods.previousCycle) do
            union[hash] = true
        end
    end

    return union
end


-- shared, onlyMine (nazwy), onlyPartnerCount, remoteCount
function Mods.compare()

    local remote = Mods.remoteSet()

    if remote == nil then
        return nil
    end

    local shared = 0
    local onlyMine = {}
    local remoteCount = 0
    local onlyPartner = 0

    for _, hash in ipairs(Mods.hashes) do

        if remote[hash] then
            shared = shared + 1
        else
            onlyMine[#onlyMine + 1] = Mods.localSet[hash]
        end
    end

    for hash in pairs(remote) do

        remoteCount = remoteCount + 1

        if Mods.localSet[hash] == nil then
            onlyPartner = onlyPartner + 1
        end
    end

    return shared, onlyMine, onlyPartner, remoteCount
end


function Mods.logComparison()

    local shared, onlyMine, onlyPartner, remoteCount =
        Mods.compare()

    if shared == nil then
        return
    end

    Diag.log(
        string.format(
            "[CP2077Coop] EVENT mods compared: you=%d partner=%d shared=%d only_you=%d only_partner=%d",
            #Mods.hashes,
            remoteCount,
            shared,
            #onlyMine,
            onlyPartner
        )
    )

    for _, name in ipairs(onlyMine) do
        Diag.log("[CP2077Coop] MOD only you: " .. name)
    end
end


-- Wczytanie gry / zmiana roli: lista drugiej strony od nowa. Nasza
-- seria startuje też: druga strona widzi jej start po ciszy i wysyła
-- nam swoją listę (Mods.notePeerHigh), nawet bez przerwy w pakietach.
function Mods.resetRemote()

    Mods.pendingHigh = nil
    Mods.building = {}
    Mods.lastCycle = nil
    Mods.previousCycle = nil
    Mods.cyclesReceived = 0
    Mods.compared = false
    Mods.peerSendsMods = false
    Mods.lastPeerHighClock = nil
    Mods.peerWasQuiet = true

    Mods.arm()
end


------------------------------------------------------------
-- GAMEPLAY / WORLD STATE SYNC
--
-- Protokół DLL przenosi tylko pozycję i kierunek (fx, fy).
-- Kierunek to wektor o długości 1, więc jego DŁUGOŚĆ niesie
-- dodatkową liczbę: długość = 1 + (typ * 512 + wartość).
-- Odbiorca odzyskuje liczbę i normalizuje kierunek.
-- Serwer przekazuje te floaty bez zmian (sprawdzone).
--
-- typ 0: flagi gracza (kucanie, broń, celowanie, strzał, pojazd)
-- typ 1: godzina gry / 3 min (tylko host)
-- typ 2: indeks pogody + 1 (tylko host, 0 = nieznana)
-- typ 3 / 4: ping / pong, typ 5: model pojazdu, typ 6 / 7: mody
-- Który typ w którym pakiecie: Sync.buildPayload.
--
-- Wszystko w tabeli Sync: LuaJIT pozwala max 60 upvalues.
------------------------------------------------------------

local Sync = {
    TYPE_FLAGS = 0,
    TYPE_TIME = 1,
    TYPE_WEATHER = 2,
    -- ping/pong: prawdziwe opóźnienie gracz -> serwer -> gracz
    TYPE_PING = 3,
    TYPE_PONG = 4,
    -- indeks pojazdu z listy w vehicle.reds (0 = spoza listy)
    TYPE_VEHICLE = 5,
    TYPE_STRIDE = 512,

    FLAG_CROUCH = 1,
    FLAG_WEAPON_DRAWN = 2,
    FLAG_AIMING = 4,
    FLAG_FIRING = 8,
    FLAG_IN_VEHICLE = 16,
    WEAPON_CLASS_MULTIPLIER = 32,
    -- bit roli (dodawany w Lua): wykrywa dwóch hostów / dwóch joinerów
    FLAG_HOST = 256,

    PING_INTERVAL = 1.0,
    -- wygładzanie RTT (0..1, większe = szybsza reakcja)
    RTT_SMOOTHING = 0.25,

    -- Teleport joinera do hosta (Sync.updateJoin). Test na żywo
    -- 2026-10-04: teleport ~8 s po wczytaniu, 8 wywołań w 2 s, gracz
    -- nie ruszył się ani o metr (WORLD SYNC FAILED 14.42 -> 12.31 ->
    -- 9.44 m, bo host-bot szedł dalej). Teraz: start dopiero, gdy gracz
    -- stoi w grze JOIN_SETTLE_SECONDS bez przerwy (nie w aucie, nie
    -- w scenie, bez skoku pozycji), JEDEN teleport na próbę, do
    -- JOIN_APPLY_TIMEOUT s czekania na zmianę pozycji, wynik mierzony
    -- do punktu teleportu (nie do hosta, który się rusza), przerwa
    -- JOIN_RETRY_DELAYS[n] s przed kolejną próbą.
    MAX_JOIN_ATTEMPTS = 3,
    JOIN_SETTLE_SECONDS = 4.0,
    JOIN_APPLY_TIMEOUT = 2.5,
    JOIN_RETRY_DELAYS = { 2.0, 4.0 },
    -- tak blisko punktu teleportu = na miejscu
    JOIN_TOLERANCE = 2.5,
    -- mniej = pozycja się nie zmieniła (teleport zignorowany)
    JOIN_MOVED_EPSILON = 0.5,
    -- skok o tyle w jednej klatce, nie przez nasz teleport = gra
    -- jeszcze ustawia gracza (ładowanie, szybka podróż)
    JOIN_JUMP_DISTANCE = 10.0,
    -- scena trzyma gracza od tego tieru (CP2077Coop_GetSceneTier)
    JOIN_SCENE_TIER = 3,

    -- Spawn avatara w miejscu drugiego gracza (Sync.spawnPoint), gdy jest
    -- najwyżej tyle metrów od nas (dalej świat może nie być wczytany:
    -- wtedy jak dawniej 2.5 m przed lokalnym graczem)
    SPAWN_AT_PARTNER_RANGE = 100.0,
    -- najdłużej tyle s czekamy, aż gra ustawi nowy avatar (0, 0, 0)
    SPAWN_PLACE_WAIT = 3.0,
    -- tyle s po spawnie / wyjściu z auta teleport nie liczy się jako korekta
    SPAWN_GRACE = 3.0,

    clock = 0.0,

    localFlags = 0,
    -- flagi prawdziwego gracza, bez nakładki bota testowego
    realLocalFlags = 0,

    pingToken = 0,
    pingSentAt = nil,
    lastPingClock = -100.0,
    pendingPong = nil,
    rttMs = nil,
    rttLastMs = nil,
    rttMinMs = nil,
    rttMaxMs = nil,
    rttSamples = 0,

    TIME_STEP_MINUTES = 3,
    -- joiner poprawia czas, gdy różnica przekracza tyle minut
    TIME_TOLERANCE_MINUTES = 6,
    TIME_APPLY_COOLDOWN = 5.0,

    -- Harmonogram payloadu (Sync.buildPayload): godzina, pogoda (host)
    -- i model pojazdu (w aucie) co WORLD_INTERVAL s i od razu po zmianie.
    -- Terminy (Sync.clock) i ostatnio wysłane wartości (nil = jeszcze
    -- nie, czyli wysłać od razu).
    WORLD_INTERVAL = 1.0,
    -- nowy model pojazdu idzie drugi raz po tylu sekundach, jak zmiana
    -- (bez czekania na budżet slotów): zgubiony albo nadpisany pierwszy
    -- pakiet kosztuje 0.2 s, nie sekundę i więcej
    VEHICLE_RESEND = 0.2,
    -- Sync.clock drugiego wysłania nowego modelu (nil = już poszło)
    vehicleResendAt = nil,
    nextTimeAt = 0.0,
    nextWeatherAt = 0.0,
    nextVehicleAt = 0.0,
    sentTime = nil,
    sentWeather = nil,
    sentVehicle = nil,
    -- poprzedni slot niósł coś innego niż flagi: ten niesie flagi
    lastSlotExtra = false,
    -- Budżet slotów bez flag (flagi w >= 85% slotów przy każdym fps,
    -- w aucie też): każdy slot dodaje EXTRA_SHARE kredytu (do
    -- EXTRA_CREDIT_MAX), każdy slot bez flag zabiera 1 (dług do
    -- -EXTRA_CREDIT_MAX; seria modów nic nie zabiera). Ping i powtórki
    -- godziny / pogody / pojazdu czekają na kredyt >= 1. 0.15 dałoby
    -- 84.99% przy niskim fps.
    EXTRA_SHARE = 0.145,
    EXTRA_CREDIT_MAX = 2.0,
    extraCredit = 1.0,
    -- flagi z ostatniego pakietu flag (model pojazdu dopiero po
    -- fladze "w pojeździe", inaczej odbiorca go wyrzuca)
    lastSentFlags = 0,

    remoteFlags = 0,
    appliedFlags = -1,
    -- druga strona wysłała ping/pong = wersja z bitem roli (FLAG_HOST
    -- przyszedł razem z ping/pong); starsza wersja wysyła flagi bez
    -- tego bitu, więc wyglądałaby na joinera
    peerHasRoleBit = false,

    -- Wersja drugiej strony a długość wektora kierunku. v0.0.26 nie
    -- dekoduje payloadu i porównuje surowe wektory: zmienna długość
    -- (flagi / godzina / pogoda / mody na zmianę) = cancel + AIRotateTo
    -- co 0.1 s u stojącego avatara. Dopóki druga strona nie wyśle
    -- payloadu > 0 (v0.0.27+), wysyłamy same flagi (stała długość, bit
    -- roli od razu); po LEGACY_AFTER_PACKETS pakietach bez payloadu to
    -- v0.0.26: payload 0 jak u niej i ping co PING_INTERVAL_LEGACY.
    peerDecodesPayload = false,
    zeroPayloadPackets = 0,
    LEGACY_AFTER_PACKETS = 150,
    PING_INTERVAL_LEGACY = 10.0,

    remoteTimeMinutes = -1,
    -- Sync.clock przy ostatnim pakiecie z godziną hosta
    remoteTimeClock = -100.0,
    -- starsza godzina = host milczy (wyjście, crash, ładowanie):
    -- nie cofamy zegara joinera do zamrożonej wartości. Host przy
    -- ~20 fps w aucie powtarza godzinę co 2-5 s (budżet slotów), więc
    -- 10 s; to ~1.3 minuty gry, daleko w TIME_TOLERANCE_MINUTES.
    TIME_FRESH_SECONDS = 10.0,
    timeCooldown = 0.0,

    appliedWeather = -1,
    -- liczba pogód w CP2077Coop_WeatherNames (state.reds); większy
    -- indeks w pakiecie = uszkodzony pakiet
    WEATHER_COUNT = 9,
    -- SetWeather (priorytet 5) trzyma pogodę hosta aż do ResetWeather:
    -- czy to my ją wymusiliśmy (tylko wtedy ją zwalniamy)
    weatherForced = false,
    -- Sync.clock przy ostatnim znanym indeksie pogody hosta
    weatherKnownClock = -100.0,
    -- tyle s bez znanej pogody hosta (host milczy albo ma pogodę
    -- spoza listy, np. z questu) = pogoda wraca do gry
    WEATHER_RELEASE_AFTER = 15.0,
    -- gra odmówiła SetWeather: ponowienie po tylu sekundach
    weatherRetry = 0.0,
    WEATHER_RETRY = 5.0,

    scriptsReported = false
}


-- Czy redscript się skompilował. Kompilacja jest "wszystko albo nic"
-- (błąd w .reds innego moda = brak wszystkich metod CP2077Coop_*),
-- więc wystarczy sprawdzić jedną metodę z state.reds.
function Sync.hasScripts(player)

    return player.CP2077Coop_GetStateFlags ~= nil
end


-- Raz na sesję: bez redscriptu nie ma avatara ani stanu gry,
-- a wywołanie brakującej metody rzucałoby błąd przy każdym pakiecie.
function Sync.reportMissingScripts()

    if Sync.scriptsReported then
        return
    end

    Sync.scriptsReported = true

    Diag.log(
        "[CP2077Coop] redscript not compiled - remote avatar and state sync disabled (check r6/logs/redscript_rCURRENT.log; an error in any mod's .reds breaks the whole compile)"
    )
end


-- Druga strona to v0.0.26: same pakiety z payloadem 0 (bez pingów,
-- flag z bitem roli, godziny, pogody, modów) przez LEGACY_AFTER_PACKETS.
function Sync.peerIsLegacy()

    return
        not Sync.peerDecodesPayload
        and Sync.zeroPayloadPackets >= Sync.LEGACY_AFTER_PACKETS
end


-- Restart drugiej gry: to może być już inna wersja moda.
function Sync.forgetPeerVersion()

    Sync.peerDecodesPayload = false
    Sync.zeroPayloadPackets = 0
end


-- Następny termin pakietu okresowego (godzina, pogoda, pojazd).
-- Spóźnienie krótsze niż interwał (sloty co 33 ms, przerwa na flagi)
-- nie przesuwa siatki, więc średnio dokładnie 1/interval Hz. Wysyłka
-- przed terminem (zmiana wartości) albo długo po nim (menu): od teraz.
function Sync.nextSendAt(dueAt, interval)

    local late =
        Sync.clock -
        dueAt

    if late >= 0.0
        and late < interval
    then
        return dueAt + interval
    end

    return
        Sync.clock +
        interval
end


-- Flagi gracza (z bitem roli) do panelu, blokady teleportu w aucie
-- i do pakietu. flagsOverride: bot testowy; blokada teleportu i poza
-- auta patrzą na prawdziwe flagi (realLocalFlags).
function Sync.readLocalFlags(player, isHost)

    local roleFlag = 0

    if isHost then
        roleFlag = Sync.FLAG_HOST
    end

    local realFlags = 0

    if Sync.hasScripts(player) then
        realFlags = player:CP2077Coop_GetStateFlags()
    else
        Sync.reportMissingScripts()
    end

    Sync.realLocalFlags =
        realFlags +
        roleFlag

    Sync.localFlags =
        (Sync.flagsOverride or realFlags) +
        roleFlag

    -- v0.0.26 porównuje surowe wektory: stała długość
    if Sync.peerIsLegacy() then
        return 0
    end

    return
        Sync.TYPE_FLAGS * Sync.TYPE_STRIDE +
        Sync.localFlags
end


-- Godzina, pogoda i model pojazdu: wartość do wysłania teraz albo nil.
-- changedOnly = tylko zmieniona (idzie przed pingiem), inaczej też ta,
-- której termin minął (co WORLD_INTERVAL). value nil = brak wartości.
function Sync.dueValue(value, sent, dueAt, changedOnly)

    if value == nil then
        return false
    end

    if value ~= sent then
        return true
    end

    return
        not changedOnly
        and Sync.clock >= dueAt
end


-- Godzina i pogoda hosta (timeValue, weatherValue: odczyt z tego slotu).
function Sync.nextWorldPayload(timeValue, weatherValue, changedOnly)

    if Sync.dueValue(timeValue, Sync.sentTime, Sync.nextTimeAt, changedOnly) then

        Sync.sentTime = timeValue
        Sync.nextTimeAt = Sync.nextSendAt(Sync.nextTimeAt, Sync.WORLD_INTERVAL)

        return
            Sync.TYPE_TIME * Sync.TYPE_STRIDE +
            timeValue
    end

    if Sync.dueValue(weatherValue, Sync.sentWeather, Sync.nextWeatherAt, changedOnly) then

        Sync.sentWeather = weatherValue
        Sync.nextWeatherAt = Sync.nextSendAt(Sync.nextWeatherAt, Sync.WORLD_INTERVAL)

        return
            Sync.TYPE_WEATHER * Sync.TYPE_STRIDE +
            weatherValue
    end

    return nil
end


-- Model auta, którym jedziemy (nil = nie jedziemy albo jeszcze nie
-- wysłaliśmy flagi "w pojeździe" - odbiorca bez niej wyrzuca model
-- w Sync.updateRemoteVehicle i czekałby na następny).
function Sync.mountedVehicleIndex(player)

    if not Sync.hasFlag(Sync.localFlags, Sync.FLAG_IN_VEHICLE) then

        Sync.sentVehicle = nil
        Sync.vehicleResendAt = nil
        return nil
    end

    if not Sync.hasFlag(Sync.lastSentFlags, Sync.FLAG_IN_VEHICLE) then
        return nil
    end

    local index = Sync.vehicleIndexOverride

    if index == nil
        and player.CP2077Coop_GetMountedVehicleIndex ~= nil
    then
        index = player:CP2077Coop_GetMountedVehicleIndex()
    end

    if index == nil
        or index < 0
    then
        return nil
    end

    return index
end


function Sync.nextVehiclePayload(index, changedOnly)

    local resend =
        index ~= nil
        and index == Sync.sentVehicle
        and Sync.vehicleResendAt ~= nil
        and Sync.clock >= Sync.vehicleResendAt

    if not resend
        and not Sync.dueValue(index, Sync.sentVehicle, Sync.nextVehicleAt, changedOnly)
    then
        return nil
    end

    if index ~= Sync.sentVehicle then
        Sync.vehicleResendAt = Sync.clock + Sync.VEHICLE_RESEND
    else
        Sync.vehicleResendAt = nil
    end

    Sync.nextVehicleAt = Sync.nextSendAt(Sync.nextVehicleAt, Sync.WORLD_INTERVAL)
    Sync.sentVehicle = index

    return
        Sync.TYPE_VEHICLE * Sync.TYPE_STRIDE +
        index
end


-- Powtórka godziny, pogody albo modelu pojazdu: ta, której termin
-- minął najdawniej (przy budżecie poniżej 1 Hz na każdą zwalniają
-- równo, żadna nie głoduje). nil = nic nie jest należne.
function Sync.nextRepeatPayload(timeValue, weatherValue, vehicleIndex)

    local due = nil
    local dueAt = nil

    if timeValue ~= nil and Sync.clock >= Sync.nextTimeAt then
        due, dueAt = "time", Sync.nextTimeAt
    end

    if weatherValue ~= nil
        and Sync.clock >= Sync.nextWeatherAt
        and (dueAt == nil or Sync.nextWeatherAt < dueAt)
    then
        due, dueAt = "weather", Sync.nextWeatherAt
    end

    if vehicleIndex ~= nil
        and Sync.clock >= Sync.nextVehicleAt
        and (dueAt == nil or Sync.nextVehicleAt < dueAt)
    then
        due = "vehicle"
    end

    if due == "time" then
        return Sync.nextWorldPayload(timeValue, nil, false)
    end

    if due == "weather" then
        return Sync.nextWorldPayload(nil, weatherValue, false)
    end

    if due == "vehicle" then
        return Sync.nextVehiclePayload(vehicleIndex, false)
    end

    return nil
end


-- Payload inny niż flagi, jeśli coś jest do wysłania (nil = flagi).
-- Kolejność: zmieniona godzina / pogoda / auto, ping, ich termin co
-- WORLD_INTERVAL (ping i powtórki tylko przy kredycie >= 1), mody.
function Sync.nextExtraPayload(player, isHost)

    -- druga strona jeszcze nie pokazała, że dekoduje payload (patrz
    -- peerDecodesPayload), albo brak redscriptu: tylko ping i flagi
    local ready =
        Sync.peerDecodesPayload
        and Sync.hasScripts(player)

    local timeValue = nil
    local weatherValue = nil
    local vehicleIndex = nil

    if ready then

        if isHost then

            timeValue =
                math.floor(
                    player:CP2077Coop_GetTimeOfDayMinutes() /
                    Sync.TIME_STEP_MINUTES
                )

            weatherValue =
                player:CP2077Coop_GetWeatherIndex() + 1
        end

        vehicleIndex =
            Sync.mountedVehicleIndex(player)

        -- zmiana: od razu, przed pingiem
        local changed =
            Sync.nextWorldPayload(timeValue, weatherValue, true)
            or Sync.nextVehiclePayload(vehicleIndex, true)

        if changed ~= nil then
            return changed
        end
    end

    -- stara wersja nie odpowiada na ping, a każdy ping to dwie zmiany
    -- długości wektora (obrót avatara u niej): rzadko, tylko żeby
    -- wykryć pomyłkę (np. kilka zgubionych pingów nowej wersji)
    local pingInterval =
        Sync.peerIsLegacy()
        and Sync.PING_INTERVAL_LEGACY
        or Sync.PING_INTERVAL

    -- ping i powtórki tylko w ramach budżetu (Sync.buildPayload)
    local affordable =
        Sync.extraCredit >= 1.0

    -- ping idzie też, zanim druga strona pokaże, że dekoduje payload:
    -- z niego druga strona wie, że my dekodujemy
    if affordable
        and Sync.clock - Sync.lastPingClock >= pingInterval
    then

        Sync.lastPingClock = Sync.clock

        Sync.pingToken =
            (Sync.pingToken + 1) %
            Sync.TYPE_STRIDE

        Sync.pingSentAt = Sync.clock

        return
            Sync.TYPE_PING * Sync.TYPE_STRIDE +
            Sync.pingToken
    end

    if not ready then
        return nil
    end

    if affordable then

        local periodic =
            Sync.nextRepeatPayload(timeValue, weatherValue, vehicleIndex)

        if periodic ~= nil then
            return periodic
        end
    end

    if Mods.due(Sync.clock) then
        return Mods.nextPayload()
    end

    return nil
end


-- Co wysłać w tym pakiecie (jeden payload na klatkę z tyknięciem
-- 30 Hz). Flagi gracza mają dojść w >= 85% pakietów u OBU ról (Total
-- Sync Plan, faza 0; wcześniej host 23%, joiner 70%: mody 25% na
-- zawsze, godzina i pogoda 50% pakietów hosta), przy każdym fps i
-- w aucie: najwyżej EXTRA_SHARE (14.5%) slotów bez flag. Teraz:
--   pong od razu (dokładny RTT),
--   godzina i pogoda (host), model pojazdu: od razu po zmianie
--     (przed pingiem),
--   ping co PING_INTERVAL, potem powtórki godziny / pogody / pojazdu
--     co WORLD_INTERVAL - jeśli starcza kredytu (poniżej ~28 slotów/s
--     albo w aucie rzadziej niż 1 Hz),
--   mody: seria do porównania + 2 cykle, potem para co 10 s (Mods.due),
--   reszta: flagi.
-- Po pakiecie bez flag (poza pongiem) następny niesie flagi, więc
-- zmiana flag czeka najwyżej jeden slot.
function Sync.buildPayload(player, isHost)

    Sync.extraCredit =
        math.min(
            Sync.EXTRA_CREDIT_MAX,
            Sync.extraCredit + Sync.EXTRA_SHARE
        )

    -- odpowiedź na ping drugiego gracza ma pierwszeństwo
    if Sync.pendingPong ~= nil then

        local token = Sync.pendingPong
        Sync.pendingPong = nil
        Sync.lastSlotExtra = true
        Sync.spendExtraSlot()

        return
            Sync.TYPE_PONG * Sync.TYPE_STRIDE +
            token
    end

    local flagsPayload =
        Sync.readLocalFlags(player, isHost)

    if not Sync.lastSlotExtra then

        -- seria modów nic nie kosztuje: inaczej po długiej liście
        -- ping i godzina czekałyby, aż dług spłaci się sam
        local bursting = Mods.bursting()

        local extra =
            Sync.nextExtraPayload(player, isHost)

        if extra ~= nil then

            local extraType =
                math.floor(extra / Sync.TYPE_STRIDE)

            if not (bursting
                and (extraType == Mods.TYPE_HI or extraType == Mods.TYPE_LO))
            then
                Sync.spendExtraSlot()
            end

            Sync.lastSlotExtra = true
            return extra
        end
    end

    Sync.lastSlotExtra = false
    Sync.lastSentFlags = Sync.localFlags

    return flagsPayload
end


-- Slot bez flag zabiera kredyt (dług ograniczony, żeby po serii
-- pongów / zmian powtórki nie czekały dłużej niż ~0.7 s).
function Sync.spendExtraSlot()

    Sync.extraCredit =
        math.max(
            -Sync.EXTRA_CREDIT_MAX,
            Sync.extraCredit - 1.0
        )
end


function Sync.encodeForward(forwardX, forwardY, payload)

    local scale =
        1.0 + payload

    return
        forwardX * scale,
        forwardY * scale
end


-- Zwraca znormalizowany kierunek i payload (nil = brak danych).
function Sync.decodeForward(rawX, rawY)

    local length =
        math.sqrt(
            rawX * rawX +
            rawY * rawY
        )

    if length < 0.5 then
        return 0.0, 1.0, nil
    end

    local payload =
        math.floor(
            length - 1.0 + 0.5
        )

    return
        rawX / length,
        rawY / length,
        payload
end


-- Yaw (stopnie) dla kierunku w poziomie, tak jak liczy go gra:
-- (0, 1) = 0°, obrót przeciwnie do ruchu wskazówek zegara, (-1, 0) = 90°.
function Sync.yawFromForward(forwardX, forwardY)

    return
        math.deg(
            math.atan2(
                -forwardX,
                forwardY
            )
        )
end


function Sync.receivePayload(payload)

    if payload == nil then
        return
    end

    -- payload > 0 (ping, flagi hosta, godzina, pogoda, mody...) wysyła
    -- tylko wersja, która go dekoduje; v0.0.26 wysyła zawsze 0
    if payload > 0 then
        Sync.peerDecodesPayload = true
    elseif not Sync.peerDecodesPayload then
        Sync.zeroPayloadPackets = Sync.zeroPayloadPackets + 1
    end

    local packetType =
        math.floor(
            payload / Sync.TYPE_STRIDE
        )

    local value =
        payload %
        Sync.TYPE_STRIDE

    if packetType == Sync.TYPE_FLAGS then

        Sync.remoteFlags = value
        Sync.remoteFlagsSeen = true
        Diag.statFlagsIn = Diag.statFlagsIn + 1

    elseif packetType == Sync.TYPE_TIME then

        -- poza dobą = uszkodzony pakiet, nie godzina
        if value * Sync.TIME_STEP_MINUTES >= 1440 then
            return
        end

        Sync.remoteTimeMinutes =
            value *
            Sync.TIME_STEP_MINUTES

        Sync.remoteTimeClock = Sync.clock

    elseif packetType == Sync.TYPE_WEATHER then

        -- poza listą pogód = uszkodzony pakiet, nie pogoda
        if value > Sync.WEATHER_COUNT then
            return
        end

        Sync.remoteWeather =
            value - 1

        if Sync.remoteWeather >= 0 then
            Sync.weatherKnownClock = Sync.clock
        end

    elseif packetType == Sync.TYPE_PING then

        Sync.peerHasRoleBit = true
        Sync.pendingPong = value

    elseif packetType == Sync.TYPE_PONG then

        Sync.peerHasRoleBit = true
        Sync.onPong(value)

    elseif packetType == Sync.TYPE_VEHICLE then

        Sync.remoteVehicleIndex = value

        -- Każda wersja wysyła indeks tylko wtedy, gdy jej własne flagi
        -- mówią "w pojeździe". Gdy ten pakiet flag zginął albo DLL go
        -- nadpisał, updateRemoteVehicle uznałby gracza za pieszego,
        -- wyrzucił indeks i czekał sekundę na następny. Następny pakiet
        -- flag nadpisuje remoteFlags, a starsze pakiety są odrzucane,
        -- więc to nie przetrwa wyjścia z auta.
        if not Sync.hasFlag(Sync.remoteFlags, Sync.FLAG_IN_VEHICLE) then
            Sync.remoteFlags = Sync.remoteFlags + Sync.FLAG_IN_VEHICLE
        end

    elseif packetType == Mods.TYPE_HI
        or packetType == Mods.TYPE_LO
    then

        Mods.receive(packetType, value, Sync.clock)
    end
end


function Sync.onPong(token)

    if Sync.pingSentAt == nil
        or token ~= Sync.pingToken
    then
        return
    end

    local sampleMs =
        (Sync.clock - Sync.pingSentAt) *
        1000.0

    Sync.pingSentAt = nil
    Sync.rttLastMs = sampleMs
    Sync.rttSamples = Sync.rttSamples + 1

    if Sync.rttMs == nil then

        Sync.rttMs = sampleMs
        Sync.rttMinMs = sampleMs
        Sync.rttMaxMs = sampleMs
        return
    end

    Sync.rttMs =
        Sync.rttMs +
        (sampleMs - Sync.rttMs) *
        Sync.RTT_SMOOTHING

    Sync.rttMinMs =
        math.min(
            Sync.rttMinMs,
            sampleMs
        )

    Sync.rttMaxMs =
        math.max(
            Sync.rttMaxMs,
            sampleMs
        )
end


-- Pojazd drugiego gracza (vehicle.reds spawnuje i przesuwa auto).
-- Teleport auta tylko przy pakiecie = auto o ~ping z tyłu i skacze
-- co 33 ms. Teraz każdy pakiet zapisuje pozę i prędkość, a
-- Sync.updateRemoteVehicle co klatkę stawia auto tam, gdzie gracz
-- jest TERAZ: ostatnia poza + prędkość * (wiek pakietu + opóźnienie
-- w jedną stronę), po łuku, z kierunkiem obróconym o skręt.
Sync.VEHICLE_MAX_LEAD = 0.35
-- 1/s: jak szybko pokazane auto dochodzi do pozy ekstrapolowanej
Sync.VEHICLE_SMOOTHING = 10.0
-- dalej = teleport auta bez wygładzania (szybka podróż, restart)
Sync.VEHICLE_SNAP_DISTANCE = 5.0
-- m/s; szybciej = teleport gracza, nie jazda
Sync.VEHICLE_MAX_SPEED = 120.0
-- rad/s
Sync.VEHICLE_MAX_YAW_RATE = 4.0
-- prędkość z ostatnich N+1 pakietów (szum klatek nadawcy się uśrednia)
Sync.VEHICLE_HISTORY = 4
-- większa luka w sekwencji = brak wiarygodnej prędkości
Sync.VEHICLE_MAX_GAP = 6

Sync.poseHistory = {}


-- Obrót wektora (x, y) o kąt w radianach (przeciwnie do wskazówek zegara).
function Sync.rotate2(x, y, angle)

    local cosine = math.cos(angle)
    local sine = math.sin(angle)

    return
        x * cosine - y * sine,
        x * sine + y * cosine
end


-- Każdy nowy pakiet ruchu: poza, prędkość i prędkość skrętu drugiego gracza.
-- sequence: licznik tyknięć 30 Hz ruchu (S.moveTicks, bez pakietów bojowych).
function Sync.recordRemotePose(sequence, x, y, z, forwardX, forwardY)

    local history = Sync.poseHistory
    local newest = history[#history]

    -- restart drugiego klienta albo długa przerwa: liczymy od nowa
    if newest ~= nil
        and (
            sequence <= newest.sequence
            or sequence - newest.sequence > Sync.VEHICLE_MAX_GAP
        )
    then
        history = {}
    end

    history[#history + 1] = {
        sequence = sequence,
        x = x,
        y = y,
        z = z,
        forwardX = forwardX,
        forwardY = forwardY
    }

    while #history > Sync.VEHICLE_HISTORY do
        table.remove(history, 1)
    end

    Sync.poseHistory = history

    Sync.poseVelX = 0.0
    Sync.poseVelY = 0.0
    Sync.poseVelZ = 0.0
    Sync.poseYawRate = 0.0
    Sync.poseSpan = 0.0

    local oldest = history[1]

    local span =
        (sequence - oldest.sequence) *
        SEND_INTERVAL

    if span > 0.0 then

        local velX = (x - oldest.x) / span
        local velY = (y - oldest.y) / span
        local velZ = (z - oldest.z) / span

        if math.sqrt(velX * velX + velY * velY + velZ * velZ) <=
            Sync.VEHICLE_MAX_SPEED
        then

            Sync.poseVelX = velX
            Sync.poseVelY = velY
            Sync.poseVelZ = velZ
            Sync.poseSpan = span

            local cross =
                oldest.forwardX * forwardY -
                oldest.forwardY * forwardX

            local dot =
                oldest.forwardX * forwardX +
                oldest.forwardY * forwardY

            Sync.poseYawRate =
                math.max(
                    -Sync.VEHICLE_MAX_YAW_RATE,
                    math.min(
                        Sync.VEHICLE_MAX_YAW_RATE,
                        math.atan2(cross, dot) / span
                    )
                )
        else

            -- skok (szybka podróż): bez prędkości, auto przeskoczy
            Sync.poseHistory = { history[#history] }
        end
    end

    Sync.poseX = x
    Sync.poseY = y
    Sync.poseZ = z
    Sync.poseForwardX = forwardX
    Sync.poseForwardY = forwardY
    Sync.poseClock = Sync.clock
end


-- Opóźnienie pakietu w jedną stronę (s). RTT zawiera też czekanie
-- pongu na najbliższy slot wysyłki drugiej strony i kwantyzację
-- klatek (razem ~SEND_INTERVAL), czego pakiet ruchu nie ma.
function Sync.oneWayLatency()

    if Sync.rttMs == nil then
        return 0.0
    end

    return
        math.max(
            0.0,
            Sync.rttMs / 1000.0 - SEND_INTERVAL
        ) * 0.5
end


-- Gdzie drugi gracz jest teraz: x, y, z, kierunek, prędkość w tej chwili.
-- Prędkość z kilku pakietów wskazuje kierunek ze środka tego odcinka,
-- więc obracamy ją o skręt z połowy odcinka i połowy wyprzedzenia (łuk).
function Sync.extrapolateRemotePose()

    local lead =
        (Sync.clock - Sync.poseClock) +
        Sync.oneWayLatency()

    local clamped =
        lead >= Sync.VEHICLE_MAX_LEAD

    lead =
        math.min(
            lead,
            Sync.VEHICLE_MAX_LEAD
        )

    local yawRate =
        Sync.poseYawRate

    local chordX, chordY =
        Sync.rotate2(
            Sync.poseVelX,
            Sync.poseVelY,
            yawRate * (Sync.poseSpan + lead) * 0.5
        )

    local forwardX, forwardY =
        Sync.rotate2(
            Sync.poseForwardX,
            Sync.poseForwardY,
            yawRate * lead
        )

    local velX, velY, velZ = 0.0, 0.0, 0.0

    -- po przekroczeniu limitu wyprzedzenia auto stoi w miejscu
    if not clamped then

        velX, velY =
            Sync.rotate2(
                Sync.poseVelX,
                Sync.poseVelY,
                yawRate * (Sync.poseSpan * 0.5 + lead)
            )

        velZ = Sync.poseVelZ
    end

    return
        Sync.poseX + chordX * lead,
        Sync.poseY + chordY * lead,
        Sync.poseZ + Sync.poseVelZ * lead,
        forwardX,
        forwardY,
        velX,
        velY,
        velZ,
        clamped and 0.0 or yawRate
end


-- Pozycja i kierunek do wysłania, gdy gracz siedzi w aucie: środek
-- auta (druga strona stawia tam swoją kopię auta), a nie fotel gracza.
function Sync.mountedVehiclePose(player, pos, forward)

    if player.CP2077Coop_GetMountedVehiclePose == nil then
        return pos, forward
    end

    local pose =
        player:CP2077Coop_GetMountedVehiclePose()

    if pose == nil or #pose < 5 then
        return pos, forward
    end

    return
        { x = pose[1], y = pose[2], z = pose[3], w = 1.0 },
        { x = pose[4], y = pose[5], z = 0.0 }
end


-- Ukrycie / pokazanie avatara (vehicle.reds, wymaga Codeware).
function Sync.setAvatarVisible(player, visible)

    if player.CP2077Coop_SetRemoteAvatarVisible == nil then
        return
    end

    player:CP2077Coop_SetRemoteAvatarVisible(visible)
end


-- Auto znika (reset, utrata połączenia, restart drugiego klienta).
-- Wróci z następnym pakietem typu pojazd.
function Sync.hideRemoteVehicle(player)

    if player ~= nil
        and player.CP2077Coop_HideRemoteVehicle ~= nil
    then
        player:CP2077Coop_HideRemoteVehicle()
    end

    Sync.vehicleShown = false
    Sync.remoteVehicleIndex = nil
    Sync.carX = nil
end


function Sync.tick(delta)

    Sync.clock =
        Sync.clock +
        delta
end


-- Lokalna pauza. CET wywołuje onUpdate także w menu ESC, na mapie
-- i w ekwipunku, z deltą czasu rzeczywistego, a IsPreGame zostaje false.
-- Świat wtedy stoi: teleport i komendy AI czekają, więc próby joina,
-- snap po spawnie i korekty liczyłyby się na pusto (WORLD SYNC GAVE UP
-- w menu, fałszywe NOT RESPONDING, zawyżone hard_per_min).
-- Menu ESC i shardy: PauseGame() -> IsGamePaused(). Hub (mapa,
-- ekwipunek, dziennik), koło broni, radio, smart frame i okna samouczka:
-- dylatacja czasu pod tymi powodami (time.tweak), IsGamePaused = false.
Sync.FROZEN_REASONS = { "hubMenu", "radial", "vehicleRadioMenu", "smartFrameMenu", "UI_TutorialPopup" }
Sync.FROZEN_CHECK_INTERVAL = 0.1
-- nil = świat działa, inaczej opis ("in the pause menu", "in a menu (...)")
Sync.frozen = nil
Sync.frozenCheckIn = 0.0
Sync.frozenNames = nil
Sync.frozenErrorLogged = false


function Sync.frozenReason()

    local requests =
        Game.GetSystemRequestsHandler()

    if requests ~= nil
        and requests.IsGamePaused ~= nil
        and requests:IsGamePaused()
    then
        return "in the pause menu"
    end

    if Game.GetTimeSystem == nil then
        return nil
    end

    local timeSystem =
        Game.GetTimeSystem()

    if timeSystem == nil
        or timeSystem.IsTimeDilationActive == nil
    then
        return nil
    end

    if Sync.frozenNames == nil then

        Sync.frozenNames = {}

        for index, name in ipairs(Sync.FROZEN_REASONS) do
            Sync.frozenNames[index] = CName.new(name)
        end
    end

    for index, name in ipairs(Sync.frozenNames) do

        if timeSystem:IsTimeDilationActive(name) then
            return "in a menu (" .. Sync.FROZEN_REASONS[index] .. ")"
        end
    end

    return nil
end


-- Co klatkę (sprawdzenie co FROZEN_CHECK_INTERVAL): zmiana stanu do
-- logu; czas w menu nie liczy się do limitu czekania na spawn.
function Sync.updateFrozen(delta)

    Sync.frozenCheckIn =
        Sync.frozenCheckIn -
        delta

    if Sync.frozenCheckIn <= 0.0 then

        Sync.frozenCheckIn =
            Sync.FROZEN_CHECK_INTERVAL

        local ok, reason =
            pcall(Sync.frozenReason)

        if not ok then

            if not Sync.frozenErrorLogged then

                Sync.frozenErrorLogged = true
                Diag.log("[CP2077Coop] pause check failed, treated as running: " .. tostring(reason))
            end

            reason = nil
        end

        if reason ~= Sync.frozen then

            if reason ~= nil then
                Diag.log("[CP2077Coop] EVENT local world frozen: you are " .. reason)
            else
                Diag.log("[CP2077Coop] EVENT local world running again")
            end

            Sync.frozen = reason
        end
    end

    if Sync.frozen ~= nil
        and S.spawnRequestedAt ~= nil
    then
        S.spawnRequestedAt =
            S.spawnRequestedAt +
            delta
    end
end


function Sync.hasFlag(flags, flag)

    return
        math.floor(flags / flag) % 2 == 1
end


function Sync.weaponClass(flags)

    -- 3 bity klasy broni; bit roli (256) leży wyżej
    return
        math.floor(
            flags /
            Sync.WEAPON_CLASS_MULTIPLIER
        ) % 8
end


function Sync.isRemoteCrouching()

    return
        Sync.hasFlag(
            Sync.remoteFlags,
            Sync.FLAG_CROUCH
        )
end


-- Kucanie i broń avatara: tylko przy zmianie.
function Sync.applyRemoteFlags(player)

    if not Sync.hasScripts(player) then
        return
    end

    local flags =
        Sync.remoteFlags

    local previous =
        Sync.appliedFlags

    if flags == previous then
        return
    end

    Sync.appliedFlags = flags


    local crouch =
        Sync.hasFlag(flags, Sync.FLAG_CROUCH)

    if previous < 0
        or crouch ~= Sync.hasFlag(previous, Sync.FLAG_CROUCH)
    then

        player:CP2077Coop_ApplyRemoteStance(
            crouch
        )
    end


    local drawn =
        Sync.hasFlag(flags, Sync.FLAG_WEAPON_DRAWN)

    local weaponClass =
        Sync.weaponClass(flags)

    local weaponChanged =
        previous < 0
        or drawn ~= Sync.hasFlag(previous, Sync.FLAG_WEAPON_DRAWN)
        or weaponClass ~= Sync.weaponClass(previous)

    if weaponChanged then

        player:CP2077Coop_ApplyRemoteWeapon(
            weaponClass,
            drawn
        )
    end
end


-- Godzina hosta: co TIME_APPLY_COOLDOWN, gdy różnica > TIME_TOLERANCE_MINUTES.
function Sync.applyRemoteTime(player, delta)

    Sync.timeCooldown =
        math.max(
            0.0,
            Sync.timeCooldown - delta
        )

    -- stara godzina (host milczy): HasRemotePlayer w DLL zostaje true,
    -- więc bez tego joiner co ~55 s cofał zegar do zamrożonej wartości.
    -- Cooldown zostaje, więc pierwszy świeży pakiet działa od razu.
    local fresh =
        Sync.clock - Sync.remoteTimeClock <
        Sync.TIME_FRESH_SECONDS

    if Sync.remoteTimeMinutes < 0
        or not fresh
        or Sync.timeCooldown > 0.0
    then
        return
    end

    Sync.timeCooldown =
        Sync.TIME_APPLY_COOLDOWN

    local localMinutes =
        player:CP2077Coop_GetTimeOfDayMinutes()

    -- najkrótsza różnica ze znakiem na zegarze 24h (joiner 23:59,
    -- host 00:01 = +2 min); state.reds przestawia zegar o ten krok
    local difference =
        (Sync.remoteTimeMinutes - localMinutes + 720) % 1440 -
        720

    if math.abs(difference) <=
        Sync.TIME_TOLERANCE_MINUTES
    then
        return
    end

    player:CP2077Coop_SetTimeOfDayMinutes(
        Sync.remoteTimeMinutes
    )

    -- duży krok wstecz (np. host wczytał save) widać w logu
    Diag.log(
        string.format(
            "[CP2077Coop] time synced to host %02d:%02d (%+d min)",
            math.floor(Sync.remoteTimeMinutes / 60),
            Sync.remoteTimeMinutes % 60,
            difference
        )
    )
end


-- Koniec wymuszenia pogody hosta: ResetWeather, wraca cykl pogody gry.
-- Tylko pogoda wymuszona przez nas (nigdy pogoda questu ani hosta).
function Sync.releaseWeather(player, reason)

    if not Sync.weatherForced
        or player.CP2077Coop_ReleaseWeather == nil
    then
        return
    end

    Sync.weatherForced = false

    -- pogoda hosta wróci z następnym znanym indeksem
    Sync.appliedWeather = -1

    local released =
        player:CP2077Coop_ReleaseWeather()

    Diag.log(
        string.format(
            "[CP2077Coop] weather released to the game (%s)%s",
            reason,
            released == false and ", game had no override" or ""
        )
    )
end


-- Pogoda hosta: przy zmianie indeksu, z ponowieniem, gdy gra odmówi.
function Sync.applyRemoteWeather(player, delta)

    Sync.weatherRetry =
        math.max(
            0.0,
            Sync.weatherRetry - delta
        )

    -- host milczy albo ma pogodę spoza listy (quest): wcześniej
    -- wymuszona pogoda zostawała do końca sesji
    if Sync.clock - Sync.weatherKnownClock >=
        Sync.WEATHER_RELEASE_AFTER
    then

        Sync.releaseWeather(
            player,
            "no known host weather"
        )

        return
    end

    if Sync.remoteWeather == nil
        or Sync.remoteWeather < 0
        or Sync.remoteWeather == Sync.appliedWeather
        or Sync.weatherRetry > 0.0
    then
        return
    end

    -- false = gra odmówiła (zmiana obszaru, pogoda questu o wyższym
    -- priorytecie): ponowienie po WEATHER_RETRY, log raz na indeks;
    -- nil = stary state.reds bez wyniku (Void): jak przyjęta
    if player:CP2077Coop_SetWeatherIndex(Sync.remoteWeather) == false then

        Sync.weatherRetry =
            Sync.WEATHER_RETRY

        if Sync.weatherRefusedIndex ~= Sync.remoteWeather then

            Sync.weatherRefusedIndex = Sync.remoteWeather

            Diag.log(
                string.format(
                    "[CP2077Coop] weather index %d refused by the game, retrying every %.0f s",
                    Sync.remoteWeather,
                    Sync.WEATHER_RETRY
                )
            )
        end

        return
    end

    Sync.appliedWeather = Sync.remoteWeather
    Sync.weatherForced = true
    Sync.weatherRefusedIndex = nil

    Diag.log(
        "[CP2077Coop] weather synced to host, index "
        .. tostring(Sync.remoteWeather)
    )
end


-- Joiner przejmuje godzinę i pogodę hosta (co klatkę z onUpdate).
function Sync.applyWorldState(player, isHost, delta)

    if not Sync.hasScripts(player) then
        return
    end

    -- host (też po zmianie roli w panelu) nie trzyma pogody joinera
    if isHost then

        Sync.releaseWeather(
            player,
            "role is host"
        )

        return
    end

    Sync.applyRemoteTime(
        player,
        delta
    )

    Sync.applyRemoteWeather(
        player,
        delta
    )
end


-- Avatar biegnący dokładnie z prędkością gracza nigdy nie nadrobi
-- opóźnienia (ping + reakcja AI) i co chwilę teleportuje się o 6 m.
-- Gdy zostaje w tyle: o jeden bieg szybciej.
-- Histereza: wyższy bieg aż błąd spadnie poniżej CATCH_UP_RELEASE.
-- Bez niej błąd skakał wokół 2 m, bieg Run/Sprint zmieniał się
-- co chwilę, a każda zmiana to nowe AIMoveTo (AI staje i rusza).
Sync.CATCH_UP_DISTANCE = 2.0
Sync.CATCH_UP_RELEASE = 1.0
Sync.CATCH_UP_NEXT = { Walk = "Run", Run = "Sprint", Sprint = "Sprint" }
Sync.catchingUp = false

function Sync.catchUpMoveType(moveType, errorDistance)

    if errorDistance >= Sync.CATCH_UP_DISTANCE then
        Sync.catchingUp = true
    elseif errorDistance < Sync.CATCH_UP_RELEASE then
        Sync.catchingUp = false
    end

    if not Sync.catchingUp then
        return moveType
    end

    return
        Sync.CATCH_UP_NEXT[moveType] or
        moveType
end


------------------------------------------------------------
-- SPAWN AVATARA
--
-- Spawn może nie wyjść: system encji jeszcze niegotowy (świat się
-- wczytuje; remote.reds zwraca wtedy false) albo encja nigdy nie
-- pojawia się w GetTagged. Wcześniej żądanie szło raz, a avatar
-- nie pojawiał się do końca sesji ("spawning" w panelu).
------------------------------------------------------------

-- najwyżej jedno wywołanie spawnu na tyle sekund (odłożony spawn
-- nie woła CreateEntity z każdym pakietem)
Sync.SPAWN_CALL_INTERVAL = 1.0
-- brak encji po żądaniu: nowe żądanie po tyle s * numer żądania
Sync.SPAWN_RETRY_SECONDS = 3.0
Sync.SPAWN_RETRY_MAX = 30.0
-- GetTagged (tablica przy każdym wywołaniu) nie co klatkę
Sync.HANDLE_POLL_INTERVAL = 0.1


-- Pierwszy pakiet drugiego gracza (i powtórki): avatar przed lokalnym graczem.
function Sync.requestSpawn(player)

    -- redscript się nie skompilował: bez avatara (log raz),
    -- reszta onUpdate (teleport do hosta, panel) działa dalej
    if player.CP2077Coop_SpawnRemoteTest == nil then

        Sync.reportMissingScripts()
        return
    end

    if S.spawnRequestedAt ~= nil
        and Sync.clock - S.spawnRequestedAt <
            Sync.SPAWN_CALL_INTERVAL
    then
        return
    end

    S.spawnRequestedAt = Sync.clock

    local x, y, z, forwardX, forwardY =
        Sync.spawnPoint(player)

    -- false = nic nie zlecono, ponowimy z kolejnym pakietem;
    -- nil = stary remote.reds bez wyniku (Void): jak zlecony
    if player:CP2077Coop_SpawnRemoteTest(x, y, z, forwardX, forwardY) == false then

        if not S.spawnDeferredLogged then

            S.spawnDeferredLogged = true

            Diag.log("[CP2077Coop] remote spawn deferred: entity system not ready")
        end

        return
    end

    S.remoteInitialized = true
    S.spawnAttempts = S.spawnAttempts + 1

    -- pierwsze sprawdzenie GetTagged jeszcze w tej klatce
    S.handlePollAccumulator = Sync.HANDLE_POLL_INTERVAL

    Diag.log(
        string.format(
            "[CP2077Coop] remote spawn requested @ %.2f %.2f %.2f (partner at %.2f %.2f %.2f)",
            x,
            y,
            z,
            S.targetX,
            S.targetY,
            S.targetZ
        )
    )
end


-- Gdzie postawić nowy avatar: tam, gdzie drugi gracz był w ostatnim
-- pakiecie (bez przewidywania) i patrzy tak jak on. Dalej niż
-- SPAWN_AT_PARTNER_RANGE od nas: 2.5 m przed lokalnym graczem.
function Sync.spawnPoint(player)

    local own =
        player:GetWorldPosition()

    local x = S.previousRemoteX or S.targetX
    local y = S.previousRemoteY or S.targetY
    local z = S.previousRemoteZ or S.targetZ

    if x ~= nil
        and distance3(own.x, own.y, own.z, x, y, z) <=
            Sync.SPAWN_AT_PARTNER_RANGE
    then
        return x, y, z, S.remoteForwardX or 0.0, S.remoteForwardY or 0.0
    end

    local forward =
        player:GetWorldForward()

    return
        own.x + forward.x * 2.5,
        own.y + forward.y * 2.5,
        own.z,
        forward.x,
        forward.y
end


-- Encji avatara nadal nie ma: następny pakiet poprosi o spawn znowu.
-- Od drugiej powtórki najpierw kasujemy stary wpis: IsPopulated = true
-- przy pustym GetTagged zatrzymałoby każdy kolejny spawn.
function Sync.checkSpawnTimeout(player)

    local waited =
        Sync.clock -
        (S.spawnRequestedAt or Sync.clock)

    local limit =
        math.min(
            Sync.SPAWN_RETRY_MAX,
            Sync.SPAWN_RETRY_SECONDS *
            math.max(1, S.spawnAttempts)
        )

    if waited < limit then
        return
    end

    local despawn =
        S.spawnAttempts >= 2
        and player.CP2077Coop_DespawnRemote ~= nil

    if despawn then
        player:CP2077Coop_DespawnRemote()
    end

    Diag.log(
        string.format(
            "[CP2077Coop] remote avatar not found %.1f s after spawn request %d%s, requesting again",
            waited,
            S.spawnAttempts,
            despawn and " (stale entry deleted)" or ""
        )
    )

    S.remoteInitialized = false
end


function Sync.reset()

    Sync.pendingPong = nil
    Sync.pingSentAt = nil
    Sync.rttMs = nil
    Sync.rttLastMs = nil
    Sync.rttMinMs = nil
    Sync.rttMaxMs = nil
    Sync.rttSamples = 0

    -- nasza godzina, pogoda i auto od razu po wczytaniu (nie za sekundę)
    Sync.sentTime = nil
    Sync.sentWeather = nil
    Sync.sentVehicle = nil
    Sync.vehicleResendAt = nil
    Sync.lastSlotExtra = false
    Sync.lastSentFlags = 0
    Sync.extraCredit = 1.0

    Sync.remoteFlags = 0
    Sync.appliedFlags = -1
    Sync.remoteTimeMinutes = -1
    Sync.remoteTimeClock = -100.0
    Sync.remoteWeather = nil
    Sync.timeCooldown = 0.0
    -- weatherForced i weatherKnownClock zostają: wymuszona pogoda
    -- zwalnia się sama, gdy host nie wróci (WEATHER_RELEASE_AFTER),
    -- albo od razu po zmianie roli na hosta
    Sync.appliedWeather = -1
    Sync.weatherRetry = 0.0
    Sync.weatherRefusedIndex = nil
    Sync.remoteFlagsSeen = false
    Sync.peerHasRoleBit = false
    Sync.remoteVehicleIndex = nil
    Sync.vehicleShown = false
    Sync.vehicleIndexOverride = nil
    Sync.poseHistory = {}
    Sync.poseX = nil
    Sync.carX = nil
    Sync.catchingUp = false

    Mods.resetRemote()
end


------------------------------------------------------------
-- STEER: komendy ruchu avatara
--
-- Stara metoda: co 0.12 s anuluj AIMoveTo i wyślij nowe.
-- AI za każdym razem stawało i ruszało od nowa, więc avatar
-- stał przez większość czasu, zostawał w tyle i skakał
-- teleportem co 6 m. Teraz: cel daleko przed graczem
-- (prędkość * LEAD_TIME) i nowa komenda tylko gdy coś się
-- realnie zmieni.
------------------------------------------------------------

local Steer = {
    LEAD_TIME = 0.8,
    MAX_LEAD = 6.0,
    -- cos(25°): większa zmiana kierunku = nowa komenda
    REISSUE_ANGLE_COS = 0.906,
    -- avatar prawie u celu: przedłużamy
    ARRIVE_DISTANCE = 1.2,
    -- nowy cel odjechał od starego o tyle: nowa komenda
    ENDPOINT_DRIFT = 1.5,
    MIN_INTERVAL = 0.25,

    -- korekta >= TELEPORT_DISTANCE: AITeleportCommand działa z opóźnieniem,
    -- więc nie wysyłamy go co klatkę; avatar, który się nie rusza
    -- (martwy, ragdoll, zablokowany) dostaje teleport rzadziej
    HARD_CORRECT_COOLDOWN = 0.25,
    HARD_CORRECT_BACKOFF = 1.0,
    HARD_CORRECT_MAX_FAILS = 8,
    STUCK_DISTANCE = 1.0,

    -- dojście po zatrzymaniu: tyle prób z rzędu bez zbliżenia się
    -- o SETTLE_MIN_PROGRESS = cel nieosiągalny (krawędź navmesha,
    -- maska auta, stół). Wtedy jeden teleport (gdy dalej niż
    -- SETTLE_TELEPORT_ERROR albo inna wysokość), potem koniec prób
    -- i zwykły obrót. Nowa seria, gdy NPC odejdzie o SETTLE_REARM.
    SETTLE_MAX_STALLS = 3,
    SETTLE_MIN_PROGRESS = 0.10,
    SETTLE_TELEPORT_ERROR = 1.0,
    SETTLE_REARM = 0.50,

    -- obrót w bezruchu: avatar przesunięty o tyle od ostatniego
    -- obrotu dostaje nowy (jego kierunek zmienił się z ruchem)
    FACING_MOVED = 0.15,

    lastHardCorrectAt = -100.0,
    hardCorrectStreak = 0,
    stuckX = 0.0,
    stuckY = 0.0,
    stuckZ = 0.0,

    sinceIssue = 99.0,
    endX = nil,
    endY = nil,
    endZ = nil,
    dirX = 0.0,
    dirY = 0.0,
    moveType = nil,
    crouched = nil
}


-- Punkt docelowy: ostatnia znana pozycja + prędkość * LEAD_TIME.
function Steer.endpoint()

    local leadX =
        (S.remoteVelocityX or 0.0) *
        Steer.LEAD_TIME

    local leadY =
        (S.remoteVelocityY or 0.0) *
        Steer.LEAD_TIME

    local leadLength =
        math.sqrt(
            leadX * leadX +
            leadY * leadY
        )

    if leadLength > Steer.MAX_LEAD then

        leadX = leadX * Steer.MAX_LEAD / leadLength
        leadY = leadY * Steer.MAX_LEAD / leadLength
    end

    local baseX = S.previousRemoteX or S.targetX
    local baseY = S.previousRemoteY or S.targetY
    local baseZ = S.previousRemoteZ or S.targetZ

    return
        baseX + leadX,
        baseY + leadY,
        baseZ
end


function Steer.shouldReissue(current, endX, endY, endZ, moveType, crouched, delta)

    Steer.sinceIssue =
        Steer.sinceIssue +
        delta

    if Steer.sinceIssue < Steer.MIN_INTERVAL then
        return false
    end

    if Steer.endX == nil
        or moveType ~= Steer.moveType
        or crouched ~= Steer.crouched
    then
        return true
    end

    -- avatar prawie dotarł
    local toEndX = Steer.endX - current.x
    local toEndY = Steer.endY - current.y

    if math.sqrt(toEndX * toEndX + toEndY * toEndY) <
        Steer.ARRIVE_DISTANCE
    then
        return true
    end

    -- cel uciekł w bok albo do tyłu. Cel przesuwa się do przodu
    -- przy każdym ruchu na wprost (prędkość * czas) - to nie powód
    -- do nowej komendy (sprint: anulowanie co 0.25 s = szarpanie);
    -- avatar dostaje dalszy cel, gdy dojdzie (ARRIVE_DISTANCE).
    local driftX = endX - Steer.endX
    local driftY = endY - Steer.endY

    if Steer.dirX == 0.0 and Steer.dirY == 0.0 then

        if math.sqrt(driftX * driftX + driftY * driftY) >
            Steer.ENDPOINT_DRIFT
        then
            return true
        end

    else

        local sideways =
            math.abs(
                driftY * Steer.dirX -
                driftX * Steer.dirY
            )

        local along =
            driftX * Steer.dirX +
            driftY * Steer.dirY

        if sideways > Steer.ENDPOINT_DRIFT
            or along < -Steer.ENDPOINT_DRIFT
        then
            return true
        end
    end

    -- zmiana kierunku ruchu gracza (liczona tak samo jak w remember)
    local dirX, dirY =
        Steer.direction(
            { x = S.previousRemoteX or endX, y = S.previousRemoteY or endY },
            endX,
            endY
        )

    if dirX == 0.0 and dirY == 0.0 then
        return false
    end

    return
        dirX * Steer.dirX +
        dirY * Steer.dirY <
        Steer.REISSUE_ANGLE_COS
end


function Steer.direction(current, endX, endY)

    local dx = endX - current.x
    local dy = endY - current.y

    local length =
        math.sqrt(dx * dx + dy * dy)

    if length < 0.001 then
        return 0.0, 0.0
    end

    return
        dx / length,
        dy / length
end


function Steer.remember(endX, endY, endZ, moveType, crouched)

    Steer.dirX, Steer.dirY =
        Steer.direction(
            { x = S.previousRemoteX or endX, y = S.previousRemoteY or endY },
            endX,
            endY
        )

    Steer.endX = endX
    Steer.endY = endY
    Steer.endZ = endZ
    Steer.moveType = moveType
    Steer.crouched = crouched
    Steer.sinceIssue = 0.0
end


function Steer.reset()

    -- zatrzymanie / teleport: następny bieg znowu od prędkości gracza
    Sync.catchingUp = false

    Steer.endX = nil
    Steer.endY = nil
    Steer.endZ = nil
    Steer.moveType = nil
    Steer.crouched = nil
    Steer.sinceIssue = 99.0
end


-- Czy wysłać teraz teleport avatara (snapNow = szybki ruch, nowy pakiet).
-- Przy błędzie >= TELEPORT_DISTANCE: najwyżej co HARD_CORRECT_COOLDOWN,
-- a gdy avatar mimo teleportów stoi w miejscu - co HARD_CORRECT_BACKOFF.
function Steer.hardCorrectAllowed(current, errorDistance, snapNow)

    local farError =
        errorDistance >= TELEPORT_DISTANCE

    local stuck =
        Steer.hardCorrectStreak >=
        Steer.HARD_CORRECT_MAX_FAILS

    local wait =
        stuck and Steer.HARD_CORRECT_BACKOFF
        or Steer.HARD_CORRECT_COOLDOWN

    -- snapNow i tak wysyła najwyżej jeden teleport na nowy pakiet
    if (not snapNow or stuck)
        and Sync.clock - Steer.lastHardCorrectAt < wait
    then
        return false
    end

    if farError then

        -- avatar się przesunął od poprzedniej próby: teleporty działają
        if Steer.hardCorrectStreak == 0
            or distance3(
                current.x,
                current.y,
                current.z,
                Steer.stuckX,
                Steer.stuckY,
                Steer.stuckZ
            ) > Steer.STUCK_DISTANCE
        then

            Steer.hardCorrectStreak = 0
            Steer.stuckX = current.x
            Steer.stuckY = current.y
            Steer.stuckZ = current.z
        end

        Steer.hardCorrectStreak =
            Steer.hardCorrectStreak + 1

        if Steer.hardCorrectStreak ==
            Steer.HARD_CORRECT_MAX_FAILS
        then

            Diag.log(
                string.format(
                    "[CP2077Coop] REMOTE AVATAR NOT RESPONDING TO TELEPORT error=%.2f - retrying every %.1f s",
                    errorDistance,
                    Steer.HARD_CORRECT_BACKOFF
                )
            )
        end
    end

    Steer.lastHardCorrectAt = Sync.clock

    return true
end


function Steer.resetHardCorrect()

    Steer.lastHardCorrectAt = -100.0
    Steer.hardCorrectStreak = 0
end


-- Nowa seria dojścia (gracz ruszył się, zmienił miejsce postoju).
function Steer.resetSettle()

    S.settleTargetX = nil
    S.settleTargetY = nil
    S.settleTargetZ = nil
    S.settleBest = nil
    S.settleStalls = 0
    S.settleTeleported = false
    S.settleDone = false
end


-- Co klatkę w bezruchu: nowa seria, gdy cel się przesunął, albo
-- gdy NPC po zakończonej serii odszedł (popchnięty, ragdoll).
function Steer.trackSettle(settleError)

    if S.settleTargetX == nil
        or distance3(
            S.targetX,
            S.targetY,
            S.targetZ,
            S.settleTargetX,
            S.settleTargetY,
            S.settleTargetZ
        ) >= MIN_TARGET_CHANGE
    then

        Steer.resetSettle()

        S.settleTargetX = S.targetX
        S.settleTargetY = S.targetY
        S.settleTargetZ = S.targetZ

        return
    end

    -- ten sam cel, NPC odszedł po zakończonej serii: próbujemy
    -- znowu (S.settleTeleported zostaje - bez drugiego teleportu)
    if S.settleDone
        and settleError >
            S.settleBest +
            Steer.SETTLE_REARM
    then

        S.settleDone = false
        S.settleBest = nil
        S.settleStalls = 0
    end
end


-- Co IDLE_SETTLE_INTERVAL, gdy avatar stoi za daleko od celu.
-- "move" = wyślij AIMoveTo, "wait" = idzie i się zbliża,
-- "teleport" = nieosiągalny i daleko, "done" = przestań próbować.
function Steer.settleStep(settleError, verticalError)

    if S.settleBest == nil then

        S.settleBest = settleError
        S.settleStalls = 0

        return "move"
    end

    if settleError <
        S.settleBest -
        Steer.SETTLE_MIN_PROGRESS
    then

        S.settleBest = settleError
        S.settleStalls = 0

        return "wait"
    end

    S.settleStalls =
        S.settleStalls + 1

    if S.settleStalls <
        Steer.SETTLE_MAX_STALLS
    then
        return "move"
    end

    S.settleStalls = 0

    if not S.settleTeleported
        and (
            settleError > Steer.SETTLE_TELEPORT_ERROR
            or verticalError >= VERTICAL_SNAP
        )
    then

        S.settleTeleported = true
        S.settleBest = nil

        return "teleport"
    end

    -- S.settleBest zostaje: punkt odniesienia dla SETTLE_REARM
    S.settleDone = true

    return "done"
end


------------------------------------------------------------
-- COMBAT SYNC (Jakub, v0.0.25 combatfix)
--
-- combat.reds wysyła trafienie NPC przez CP2077Coop_PushPlayerState:
--   x/y/z = pozycja trafionego NPC, w = -777, forwardX = obrażenia,
--   forwardY = 9999 (znacznik). To NIE jest ruch gracza.
-- Odbiorca szuka najbliższego NPC przy tej pozycji i odejmuje HP.
-- Pakiet bojowy sprawdzamy PRZED dekodowaniem stanu z długości
-- kierunku (9999 rozwaliłoby kanał stanu).
------------------------------------------------------------

local Combat = {
    MARKER_FORWARD_Y = 9999.0,
    MARKER_TOLERANCE = 0.5,
    -- jak daleko od gracza szukamy NPC
    SCAN_RADIUS = 220.0,
    -- maks. różnica pozycji, żeby uznać NPC za tego samego
    MATCH_RADIUS = 4.0,
    MAX_DAMAGE = 5000.0,
    TRY_RAGDOLL = true,

    hitsReceived = 0,
    hitsApplied = 0,
    hitsUnmatched = 0,

    -- rodzaje błędów już zapisanych do coop_events.log (scan, damage)
    errorLogged = {}
}


-- Linie per trafienie idą tylko do print() (scripting.log): przy serii
-- z SMG (~10/s) Diag.log otwierałby i zamykał coop_events.log przy każdym
-- trafieniu i wypychał zdarzenia sesji z podglądu monitora. Liczniki są
-- w [STATS]. Błąd danego rodzaju trafia do pliku tylko raz.
function Combat.logError(kind, text)

    if Combat.errorLogged[kind] then
        print(text)
        return
    end

    Combat.errorLogged[kind] = true
    Diag.log(text)
end


function Combat.isPacket(rawForwardY)

    return
        math.abs(
            rawForwardY -
            Combat.MARKER_FORWARD_Y
        ) <= Combat.MARKER_TOLERANCE
end


function Combat.findNearestNPCAt(player, x, y, z)

    local best = nil
    local bestDistance =
        Combat.MATCH_RADIUS + 0.001

    local ok, err =
        pcall(function()

            local query =
                Game["TSQ_NPC;"]()

            query.maxDistance =
                Combat.SCAN_RADIUS

            -- CET zwraca tablicę bezpośrednio albo jako drugą wartość
            local first, second =
                Game.GetTargetingSystem():
                    GetTargetParts(player, query)

            local parts =
                second or first

            if parts == nil then
                return
            end

            for _, part in ipairs(parts) do

                pcall(function()

                    local component = part:GetComponent()

                    if component == nil then
                        return
                    end

                    local entity = component:GetEntity()

                    if entity == nil
                        or entity == S.remoteHandle
                        or not entity:IsNPC()
                        or entity:IsDead()
                    then
                        return
                    end

                    local pos = entity:GetWorldPosition()

                    local d =
                        distance3(pos.x, pos.y, pos.z, x, y, z)

                    if d < bestDistance then
                        bestDistance = d
                        best = entity
                    end
                end)
            end
        end)

    if not ok then

        Combat.logError("scan", "[CP2077Coop] COMBAT scan error: " .. tostring(err))
        return nil, nil
    end

    return best, bestDistance
end


function Combat.tryHitReaction(target)

    if not Combat.TRY_RAGDOLL then
        return
    end

    -- best-effort: dostępność w CET zależy od builda
    pcall(function()

        local event =
            Game.CreateForceRagdollEvent(
                CName.new("CP2077Coop Remote Hit")
            )

        if event ~= nil then
            target:QueueEvent(event)
        end
    end)
end


function Combat.applyRemoteHit(player, hitX, hitY, hitZ, rawDamage)

    Combat.hitsReceived =
        Combat.hitsReceived + 1

    local damage =
        math.max(
            0.0,
            math.min(rawDamage, Combat.MAX_DAMAGE)
        )

    if damage <= 0.0 then
        return
    end

    local target, targetDistance =
        Combat.findNearestNPCAt(player, hitX, hitY, hitZ)

    if target == nil then

        Combat.hitsUnmatched =
            Combat.hitsUnmatched + 1

        print(
            string.format(
                "[CP2077Coop] COMBAT HIT no NPC match @ %.2f %.2f %.2f dmg=%.2f",
                hitX, hitY, hitZ, damage
            )
        )

        return
    end

    local applied = false

    local ok, err =
        pcall(function()

            if Game.GetGodModeSystem():
                HasGodMode(
                    target:GetEntityID(),
                    gameGodModeType.Invulnerable
                )
            then
                return
            end

            Game.GetStatPoolsSystem():
                RequestChangingStatPoolValue(
                    target:GetEntityID(),
                    "Health",
                    -damage,
                    player,
                    true,
                    false
                )

            applied = true
        end)

    if not ok then

        Combat.logError("damage", "[CP2077Coop] COMBAT damage error: " .. tostring(err))
        return
    end

    if not applied then

        print("[CP2077Coop] COMBAT target invulnerable")
        return
    end

    Combat.hitsApplied =
        Combat.hitsApplied + 1

    Combat.tryHitReaction(target)

    print(
        string.format(
            "[CP2077Coop] COMBAT HIT applied dmg=%.2f match=%.2fm",
            damage,
            targetDistance or -1.0
        )
    )
end


------------------------------------------------------------
-- TEST PATTERN BOT
--
-- Do testów bez drugiej osoby: zamiast prawdziwej pozycji
-- gracza wysyłamy zaplanowaną trasę (koło 8 m obok gracza)
-- i flagi stanu. Druga instancja gry powinna pokazać avatar
-- idący, biegnący, kucający, z bronią itd.
-- Start: przycisk w panelu albo plik testpattern.txt w folderze moda.
------------------------------------------------------------

local Bot = {
    FILE = "testpattern.txt",
    RADIUS = 8.0,
    -- środek koła przesunięty od gracza, żeby avatar nie wchodził w gracza
    CENTER_OFFSET = 12.0,

    -- name, start (s), speed (m/s), flags (bez bitu roli)
    -- flags: crouch=1 drawn=2 aim=4 fire=8 vehicle=16, klasa broni *32
    PHASES = {
        { "walk",          0,  1.8, 0 },
        { "run",           6,  4.5, 0 },
        { "sprint",       12,  7.0, 0 },
        { "pistol",       15,  0.0, 2 + 1 * 32 },
        { "pistol-aim",   17,  0.0, 2 + 4 + 1 * 32 },
        { "crouch-walk",  19,  1.3, 1 },
        { "crouch-rifle", 25,  0.0, 1 + 2 + 2 * 32 },
        { "dodge",        29, 15.0, 0 },
        { "idle",         29.4, 0.0, 0 },
        { "vehicle",      34, 14.0, 16 },
        { "idle-end",     40,  0.0, 0 },
    },
    CYCLE = 44.0,
    VEHICLE_INDEX = 35,

    active = false,
    time = 0.0,
    distance = 0.0,
    phaseIndex = 0,
    anchor = nil,
    x = 0.0,
    y = 0.0,
    z = 0.0,
    forwardX = 0.0,
    forwardY = 1.0
}


function Bot.phaseAt(time)

    local current = 1

    for index, phase in ipairs(Bot.PHASES) do

        if time >= phase[2] then
            current = index
        end
    end

    return current
end


-- Środek trasy ustala Bot.update przy pierwszej klatce, w której gracz
-- jest już na miejscu (joiner: po teleporcie do hosta).
function Bot.start()

    Bot.anchor = nil
    Bot.active = true
    Bot.time = 0.0
    Bot.distance = 0.0
    Bot.phaseIndex = 0

    Diag.log("[CP2077Coop] EVENT test pattern started")
end


function Bot.stop()

    if not Bot.active then
        return
    end

    Bot.active = false
    Sync.flagsOverride = nil
    Sync.vehicleIndexOverride = nil

    Diag.log("[CP2077Coop] EVENT test pattern stopped")
end


function Bot.update(delta, player)

    if not Bot.active then
        return
    end

    -- joiner przed teleportem do hosta (także po wczytaniu zapisu i po
    -- "Teleport to host"): prawdziwa pozycja i flagi, trasa później
    if not IS_HOST
        and not S.worldJoinComplete
    then

        Bot.anchor = nil
        Sync.flagsOverride = nil
        Sync.vehicleIndexOverride = nil
        return
    end

    if Bot.anchor == nil then

        local position =
            player:GetWorldPosition()

        Bot.anchor = {
            x = position.x + Bot.CENTER_OFFSET,
            y = position.y,
            z = position.z
        }

        Bot.time = 0.0
        Bot.distance = 0.0
        Bot.phaseIndex = 0

        Diag.log(
            string.format(
                "[CP2077Coop] BOT anchored @ %.2f %.2f %.2f",
                Bot.anchor.x,
                Bot.anchor.y,
                Bot.anchor.z
            )
        )
    end

    Bot.time =
        (Bot.time + delta) %
        Bot.CYCLE

    local index =
        Bot.phaseAt(Bot.time)

    local phase =
        Bot.PHASES[index]

    if index ~= Bot.phaseIndex then

        Bot.phaseIndex = index

        Diag.log(
            string.format(
                "[CP2077Coop] BOT phase=%s speed=%.1f flags=%d",
                phase[1],
                phase[3],
                phase[4]
            )
        )
    end

    Bot.distance =
        Bot.distance +
        phase[3] *
        delta

    local angle =
        Bot.distance /
        Bot.RADIUS

    Bot.x =
        Bot.anchor.x +
        math.cos(angle) *
        Bot.RADIUS

    Bot.y =
        Bot.anchor.y +
        math.sin(angle) *
        Bot.RADIUS

    Bot.z = Bot.anchor.z

    -- kierunek = styczna do koła (ruch przeciwnie do wskazówek zegara)
    Bot.forwardX = -math.sin(angle)
    Bot.forwardY = math.cos(angle)

    Sync.flagsOverride = phase[4]

    -- faza "vehicle": Archer Hella (indeks 35 w vehicle.reds)
    if Sync.hasFlag(phase[4], Sync.FLAG_IN_VEHICLE) then
        Sync.vehicleIndexOverride = Bot.VEHICLE_INDEX
    else
        Sync.vehicleIndexOverride = nil
    end
end


function Bot.phaseName()

    if Bot.active and Bot.anchor == nil then
        return "waiting-for-join"
    end

    if not Bot.active or Bot.phaseIndex == 0 then
        return "off"
    end

    return
        Bot.PHASES[Bot.phaseIndex][1]
end


------------------------------------------------------------
-- DIAGNOSTICS: statystyki, logi, panel w grze
--
-- Panel: okno "CP2077 Coop" (widoczne zawsze, klikalne przy
-- otwartym overlayu CET). Skrót: Bindings -> "Toggle coop panel".
-- Tabela Diag i Diag.log: na początku pliku (sekcja LOG).
------------------------------------------------------------

-- Dryf avatara: odległość od miejsca, gdzie drugi gracz jest teraz
-- (ostatni pakiet + prędkość * (wiek pakietu + opóźnienie w jedną
-- stronę), najwyżej PREDICTION_TIME). Cel sterowania S.target* wyprzedza
-- gracza o PREDICTION_TIME (do 2 m), więc zawyżał odczyt przy każdym biegu.
function Diag.recordDrift(current)

    if S.previousRemoteX == nil then
        return
    end

    local lead = 0.0

    if S.remoteMoving then

        lead =
            math.min(
                (Diag.packetAge() or 0.0) + Sync.oneWayLatency(),
                PREDICTION_TIME
            )
    end

    local distance =
        distance3(
            current.x,
            current.y,
            current.z,

            S.previousRemoteX + S.remoteVelocityX * lead,
            S.previousRemoteY + S.remoteVelocityY * lead,
            S.previousRemoteZ
        )

    Diag.avatarError = distance
    Diag.driftSum = Diag.driftSum + distance
    Diag.driftCount = Diag.driftCount + 1
    Diag.driftMax = math.max(Diag.driftMax, distance)
end


function Diag.closeDriftWindow()

    if Diag.driftCount > 0 then

        Diag.driftAvgLast =
            Diag.driftSum /
            Diag.driftCount

        Diag.driftMaxLast = Diag.driftMax
    else

        Diag.driftAvgLast = nil
        Diag.driftMaxLast = nil
    end

    Diag.driftSum = 0.0
    Diag.driftCount = 0
    Diag.driftMax = 0.0
end


function Diag.loadRole()

    local file =
        io.open(Diag.ROLE_FILE, "r")

    if file == nil then
        return
    end

    local role =
        file:read("*l")

    file:close()

    if role == "host" then
        IS_HOST = true
    elseif role == "joiner" then
        IS_HOST = false
    end
end


function Diag.saveRole(isHost)

    local file =
        io.open(Diag.ROLE_FILE, "w")

    if file == nil then

        Diag.log("[CP2077Coop] could not save role.txt")
        return
    end

    if isHost then
        file:write("host\n")
    else
        file:write("joiner\n")
    end

    file:close()
end


function Diag.setRole(isHost)

    if IS_HOST == isHost then
        return
    end

    IS_HOST = isHost
    Diag.saveRole(isHost)
    Diag.roleChanged = true

    Diag.log(
        "[CP2077Coop] EVENT role changed to "
        .. (isHost and "HOST" or "JOINER")
    )
end


-- Wywoływane dla każdego nowszego pakietu.
-- Każdy odczyt slotu DLL (klatka z danymi drugiej strony).
function Diag.onPoll()

    if Diag.lastPollClock ~= nil then
        Diag.pollGap = Sync.clock - Diag.lastPollClock
    else
        Diag.pollGap = 0.0
    end

    Diag.lastPollClock = Sync.clock
end


function Diag.onPacket(sequence, previousSequence)

    Diag.packetsReceived =
        Diag.packetsReceived + 1

    Diag.statReceived =
        Diag.statReceived + 1

    Diag.windowReceived =
        Diag.windowReceived + 1

    if previousSequence >= 0 then

        Diag.windowSeqAdvance =
            Diag.windowSeqAdvance +
            math.max(0, sequence - previousSequence)
    else

        Diag.windowSeqAdvance =
            Diag.windowSeqAdvance + 1
    end

    if previousSequence >= 0
        and sequence > previousSequence + 1
    then

        local gap =
            sequence - previousSequence - 1

        -- od poprzedniego odczytu minęło tyle tyknięć nadawcy: tyle
        -- pakietów mogło przyjść naraz i nadpisać się w slocie DLL
        local overwritten =
            math.min(
                gap,
                math.max(
                    0,
                    math.ceil(Diag.pollGap / SEND_INTERVAL - 1e-6) - 1
                )
            )

        Diag.overwritten = Diag.overwritten + overwritten
        Diag.statOverwritten = Diag.statOverwritten + overwritten

        Diag.missed = Diag.missed + (gap - overwritten)
        Diag.statMissed = Diag.statMissed + (gap - overwritten)
    end

    Diag.lastPacketClock = Sync.clock
end


-- Nowa sesja (sync ON/OFF, wczytanie gry, zmiana roli): liczniki
-- i okna od zera, żeby dane sprzed menu nie udawały bieżących.
function Diag.resetSession()

    Diag.packetsReceived = 0
    Diag.missed = 0
    Diag.overwritten = 0
    Diag.ignored = 0

    Diag.statReceived = 0
    Diag.statMissed = 0
    Diag.statOverwritten = 0
    Diag.missedPctLast = nil
    Diag.overwrittenPctLast = nil

    Diag.lastPacketClock = nil
    Diag.lastPollClock = nil
    Diag.windowReceived = 0
    Diag.windowSeqAdvance = 0
    Diag.ppsIn = 0.0
    Diag.peerRate = 0.0

    Diag.avatarError = nil
    Diag.driftSum = 0.0
    Diag.driftCount = 0
    Diag.driftMax = 0.0
    Diag.driftAvgLast = nil
    Diag.driftMaxLast = nil

    Diag.statFlagsIn = 0
    Diag.flagsInLast = nil
    Diag.hardWindow = 0
    Diag.hardTotal = 0
    Diag.hardHistory = {}
    Diag.hardPerMinuteLast = nil
end


-- Teleport avatara, który poprawia błąd (hardCorrectRemote).
function Diag.onHardCorrection()

    Diag.hardWindow = Diag.hardWindow + 1
    Diag.hardTotal = Diag.hardTotal + 1
end


function Diag.onIgnored()

    Diag.ignored =
        Diag.ignored + 1

    -- spóźniony pakiet jednak doszedł: był policzony jako luka
    if Diag.missed > 0 then
        Diag.missed = Diag.missed - 1
    end

    if Diag.statMissed > 0 then
        Diag.statMissed = Diag.statMissed - 1
    end
end


-- Tyknięcia 30 Hz wysłane w jednej klatce. Wątek DLL wysyła tylko
-- najnowsze, więc kilka w klatce = jeden pakiet, a druga strona widzi
-- lukę w numerach (u niej: missed).
function Diag.onSent(ticks)

    Diag.packetsSent =
        Diag.packetsSent + ticks

    Diag.windowSent =
        Diag.windowSent + ticks

    Diag.statPushes =
        Diag.statPushes + ticks

    Diag.statMerged =
        Diag.statMerged + (ticks - 1)
end


function Diag.packetAge()

    if Diag.lastPacketClock == nil then
        return nil
    end

    return
        Sync.clock -
        Diag.lastPacketClock
end


function Diag.connectionState()

    -- lokalny gracz w menu / przy ładowaniu: nie odbieramy, to nie sieć
    if not S.syncActive then
        return "PAUSED"
    end

    local age =
        Diag.packetAge()

    if age == nil then
        return "WAITING"
    end

    if age >= Diag.LOST_AFTER then
        return "LOST"
    end

    if age >= Diag.STALE_AFTER then
        return "STALE"
    end

    return "OK"
end


-- 100 * part / total (nil = brak danych)
function Diag.percentOf(part, total)

    if total <= 0 then
        return nil
    end

    return
        100.0 *
        part /
        total
end


-- missed w całej sesji (procent numerów drugiej strony)
function Diag.missedPercent()

    return
        Diag.percentOf(
            Diag.missed,
            Diag.packetsReceived + Diag.missed + Diag.overwritten
        ) or 0.0
end


-- Procenty z ostatniego okna STATS_INTERVAL. Licznik całej sesji
-- tłumi bieżące straty: po godzinie gry minuty strat ledwo go ruszają.
function Diag.closeLossWindow()

    local expected =
        Diag.statReceived +
        Diag.statMissed +
        Diag.statOverwritten

    Diag.missedPctLast =
        Diag.percentOf(Diag.statMissed, expected)

    Diag.overwrittenPctLast =
        Diag.percentOf(Diag.statOverwritten, expected)

    Diag.mergedPctLast =
        Diag.percentOf(Diag.statMerged, Diag.statPushes)

    Diag.statReceived = 0
    Diag.statMissed = 0
    Diag.statOverwritten = 0
    Diag.statPushes = 0
    Diag.statMerged = 0
end


-- Koniec okna STATS_INTERVAL (window = jego długość w s): p99 czasu
-- klatki, flagi drugiej strony na sekundę, twarde korekty na minutę.
function Diag.closeRateWindow(window)

    local frames = Diag.frameTimes

    if #frames > 0 then

        table.sort(frames)

        Diag.frameP99Last =
            frames[math.ceil(#frames * 0.99)] *
            1000.0
    else
        Diag.frameP99Last = nil
    end

    Diag.frameTimes = {}

    Diag.flagsInLast =
        Diag.statFlagsIn /
        window

    Diag.statFlagsIn = 0

    local history = Diag.hardHistory

    history[#history + 1] = { count = Diag.hardWindow, seconds = window }

    if #history > Diag.HARD_WINDOWS then
        table.remove(history, 1)
    end

    Diag.hardWindow = 0

    local count = 0
    local seconds = 0.0

    for _, entry in ipairs(history) do

        count = count + entry.count
        seconds = seconds + entry.seconds
    end

    Diag.hardPerMinuteLast =
        count * 60.0 /
        seconds
end


function Diag.formatPct(value)

    return Diag.formatTenths(value)
end


-- jedna cyfra po przecinku, "-" = brak danych
function Diag.formatTenths(value)

    if value == nil then
        return "-"
    end

    return
        string.format(
            "%.1f",
            value
        )
end


-- Rola drugiej strony liczy się dopiero, gdy wiadomo, że wysyła bit
-- roli: starsza wersja moda (bez ping/pong) wyglądałaby na joinera
-- i panel kazałby zmienić rolę zamiast zaktualizować moda.
function Diag.roleConflict()

    if not Sync.remoteFlagsSeen
        or not Sync.peerHasRoleBit
    then
        return false
    end

    local remoteIsHost =
        Sync.hasFlag(
            Sync.remoteFlags,
            Sync.FLAG_HOST
        )

    return remoteIsHost == IS_HOST
end


-- Druga strona bez ping/pong = starsza wersja moda. Liczą się pakiety
-- tej sesji (Sync.reset czyści RTT przy każdym wczytaniu); w menu
-- i przy ładowaniu nie oceniamy.
function Diag.peerLooksOutdated()

    return
        S.syncActive
        and Diag.packetsReceived > 150
        and Sync.rttMs == nil
end


function Diag.formatMs(value)

    if value == nil then
        return "-"
    end

    return
        string.format(
            "%.0f",
            value
        )
end


-- pole porównania modów do linii STATS (1 = wspólne, 4 = partner)
function Diag.modsField(position)

    local results = { Mods.compare() }

    if results[1] == nil then
        return "-"
    end

    return tostring(results[position])
end


function Diag.statsLine()

    local age =
        Diag.lastAge

    return
        string.format(
            "[CP2077Coop] [STATS] version=%s state=%s sync=%s role=%s rtt_ms=%s rtt_min=%s rtt_max=%s rtt_n=%d pps_in=%.1f pps_out=%.1f fps=%.0f peer_rate=%.1f missed_pct=%s missed_total_pct=%.1f overwritten_pct=%s out_merged_pct=%s ignored=%d age_ms=%s avatar_err_m=%s drift_avg_m=%s drift_max_m=%s remote_speed=%.1f move=%s remote_flags=%d bot=%s hits_in=%d hits_applied=%d hits_unmatched=%d mods_you=%d mods_partner=%s mods_shared=%s conflict=%s peer_old=%s torn=%d frame_p99_ms=%s hard_per_min=%s flags_rx_ps=%s",
            Diag.VERSION,
            Diag.lastState,
            S.syncActive and "on" or "off",
            IS_HOST and "host" or "joiner",
            Diag.formatMs(Sync.rttMs),
            Diag.formatMs(Sync.rttMinMs),
            Diag.formatMs(Sync.rttMaxMs),
            Sync.rttSamples,
            Diag.ppsIn,
            Diag.ppsOut,
            Diag.fps,
            Diag.peerRate,
            Diag.formatPct(Diag.missedPctLast),
            Diag.missedPercent(),
            Diag.formatPct(Diag.overwrittenPctLast),
            Diag.formatPct(Diag.mergedPctLast),
            Diag.ignored,
            age and string.format("%.0f", age * 1000.0) or "-",
            Diag.avatarError and string.format("%.2f", Diag.avatarError) or "-",
            Diag.driftAvgLast and string.format("%.2f", Diag.driftAvgLast) or "-",
            Diag.driftMaxLast and string.format("%.2f", Diag.driftMaxLast) or "-",
            S.remoteSpeed or 0.0,
            S.remoteMoving and (S.movementType or "-") or "idle",
            Sync.remoteFlags or 0,
            Bot.phaseName(),
            Combat.hitsReceived,
            Combat.hitsApplied,
            Combat.hitsUnmatched,
            #Mods.hashes,
            Diag.modsField(4),
            Diag.modsField(1),
            tostring(Diag.roleConflict()),
            tostring(Diag.peerLooksOutdated()),
            Diag.tornReads,
            Diag.formatTenths(Diag.frameP99Last),
            Diag.formatTenths(Diag.hardPerMinuteLast),
            Diag.formatTenths(Diag.flagsInLast)
        )
end


-- Linia [STATS]: konsola CET + coop_stats_<rola>.txt (nadpisywany co
-- STATS_INTERVAL; coop_monitor.py ocenia świeżość po czasie zapisu).
function Diag.writeStats(line)

    print(line)

    Diag.writeFile(
        Diag.STATS_FILE_PREFIX .. (IS_HOST and "host" or "joiner") .. ".txt",
        "w",
        line .. "\n"
    )
end


-- Monitor (coop-tools/coop_monitor.py) zapisuje tu IP serwera i ping.
function Diag.readMonitorStatus()

    local file =
        io.open(Diag.MONITOR_FILE, "r")

    if file == nil then

        Diag.monitor = {}
        return
    end

    local values = {}

    for line in file:lines() do

        local key, value =
            string.match(
                line,
                "^([%w_]+)=(.*)$"
            )

        if key ~= nil then
            values[key] = value
        end
    end

    file:close()

    -- wiek wpisu: monitor pisze "updated=<sekundy epoki>" co 5 s. Brak
    -- albo stary znacznik = monitor zamknięty / padł; ujemny wiek ponad
    -- minutę = zegar się nie zgadza, też nie ufamy wartościom
    local updated =
        tonumber(values.updated)

    if updated == nil then

        values.stale = true
    else

        values.ageSeconds =
            os.time() - updated

        values.stale =
            values.ageSeconds > Diag.MONITOR_STALE_AFTER
            or values.ageSeconds < -60
    end

    Diag.monitor = values
end


-- Wołane na początku klatki, przed Sync.tick: zegar stoi jeszcze na
-- odczycie DLL z poprzedniej klatki. Liczone po Sync.tick, jedna długa
-- klatka (autozapis, streaming, przeciąganie okna) dodawała swój czas
-- do wieku pakietu i logowała "OK -> STALE", choć pakiety szły cały
-- czas i ta sama klatka je zaraz odczytywała.
function Diag.updateConnectionState()

    local state =
        Diag.connectionState()

    Diag.lastAge =
        Diag.packetAge()

    if state ~= Diag.lastState then

        Diag.log(
            "[CP2077Coop] EVENT connection "
            .. Diag.lastState
            .. " -> "
            .. state
        )

        Diag.lastState = state
    end
end


function Diag.tick(delta)

    Diag.windowTimer =
        Diag.windowTimer + delta

    Diag.windowFrames =
        Diag.windowFrames + 1

    local frames = Diag.frameTimes
    frames[#frames + 1] = delta

    if Diag.windowTimer >= 1.0 then

        Diag.ppsIn =
            Diag.windowReceived /
            Diag.windowTimer

        Diag.ppsOut =
            Diag.windowSent /
            Diag.windowTimer

        Diag.fps =
            Diag.windowFrames /
            Diag.windowTimer

        Diag.peerRate =
            Diag.windowSeqAdvance /
            Diag.windowTimer

        Diag.windowReceived = 0
        Diag.windowSent = 0
        Diag.windowFrames = 0
        Diag.windowSeqAdvance = 0
        Diag.windowTimer = 0.0
    end


    Diag.statsTimer =
        Diag.statsTimer + delta

    if Diag.statsTimer >= Diag.STATS_INTERVAL then

        local window = Diag.statsTimer

        Diag.statsTimer = 0.0
        Diag.closeDriftWindow()
        Diag.closeLossWindow()
        Diag.closeRateWindow(window)
        Diag.writeStats(Diag.statsLine())
    end


    Diag.monitorTimer =
        Diag.monitorTimer + delta

    if Diag.monitorTimer >= Diag.MONITOR_READ_INTERVAL then

        Diag.monitorTimer = 0.0
        Diag.readMonitorStatus()
    end
end


------------------------------------------------------------
-- PANEL (ImGui)
------------------------------------------------------------

function Diag.colorFor(level)

    if level == "good" then
        return 0.40, 0.90, 0.45, 1.0
    end

    if level == "warn" then
        return 1.00, 0.80, 0.25, 1.0
    end

    if level == "bad" then
        return 1.00, 0.35, 0.35, 1.0
    end

    return 0.80, 0.80, 0.80, 1.0
end


function Diag.row(label, value, level)

    ImGui.Text(label)
    ImGui.SameLine(150)

    local r, g, b, a =
        Diag.colorFor(level)

    ImGui.TextColored(r, g, b, a, value)
end


-- Ocena wartości wg progów z Diag.LIMITS (nil = brak danych).
function Diag.levelFor(value, limits)

    if value == nil then
        return "neutral"
    end

    if value < limits[1] then
        return "good"
    end

    if value < limits[2] then
        return "warn"
    end

    return "bad"
end


function Diag.describeFlags(flags)

    local parts = {}

    if Sync.hasFlag(flags, Sync.FLAG_CROUCH) then
        parts[#parts + 1] = "crouch"
    end

    if Sync.hasFlag(flags, Sync.FLAG_WEAPON_DRAWN) then
        parts[#parts + 1] =
            "weapon#" .. tostring(Sync.weaponClass(flags))
    end

    if Sync.hasFlag(flags, Sync.FLAG_AIMING) then
        parts[#parts + 1] = "aim"
    end

    if Sync.hasFlag(flags, Sync.FLAG_FIRING) then
        parts[#parts + 1] = "fire"
    end

    if Sync.hasFlag(flags, Sync.FLAG_IN_VEHICLE) then
        parts[#parts + 1] = "vehicle"
    end

    if #parts == 0 then
        return "idle"
    end

    return table.concat(parts, ", ")
end


function Diag.avatarState()

    if S.remoteHandle ~= nil then
        return "spawned"
    end

    if S.spawnAttempts == 0 then

        -- spawn odłożony: system encji jeszcze niegotowy
        if S.spawnRequestedAt ~= nil then
            return "waiting for world"
        end

        -- joiner: spawn dopiero po teleporcie do hosta
        if not Sync.joinAllowsSpawn() then
            return "after the join teleport"
        end

        return "none"
    end

    if S.spawnAttempts == 1 then
        return "spawning"
    end

    return string.format("spawning (request %d)", S.spawnAttempts)
end


-- Wiersz "Join" (joiner): co robi teleport do hosta (Sync.updateJoin).
function Diag.joinStatus()

    local attempt =
        string.format(
            "%d/%d",
            S.joinAttempts,
            Sync.MAX_JOIN_ATTEMPTS
        )

    if S.joinPhase == "done" then

        return
            string.format(
                "next to the host (attempt %s, %.1f m from the teleport point)",
                attempt,
                S.joinError or 0.0
            ),
            "good"
    end

    if S.joinPhase == "gave_up" then

        return
            string.format(
                "gave up after %d attempts (%s) - press 'Teleport to host'",
                S.joinAttempts,
                S.joinFailure or "?"
            ),
            "bad"
    end

    if S.joinPhase == "teleport" then

        return
            string.format(
                "attempt %s: waiting for the game to move you (%.1f / %.1f s)",
                attempt,
                math.max(0.0, Sync.clock - S.joinCalledAt - S.joinPausedFor),
                Sync.JOIN_APPLY_TIMEOUT
            ),
            "warn"
    end

    if S.joinPhase == "retry" then

        return
            string.format(
                "attempt %s failed (%s), next in %.1f s",
                attempt,
                S.joinFailure or "?",
                math.max(0.0, S.joinRetryDelay - S.joinPhaseTime)
            ),
            "warn"
    end

    if S.joinWaitReason ~= nil then
        return "waiting: you are " .. S.joinWaitReason, "warn"
    end

    if S.joinSettled < Sync.JOIN_SETTLE_SECONDS then

        return
            string.format(
                "waiting for the game to settle (%.1f / %.0f s)",
                S.joinSettled,
                Sync.JOIN_SETTLE_SECONDS
            ),
            "warn"
    end

    if S.hostSettled < Sync.JOIN_SETTLE_SECONDS
        and Diag.lastPacketClock ~= nil
    then

        return
            string.format(
                "waiting for the host to settle (%.1f / %.0f s)",
                S.hostSettled,
                Sync.JOIN_SETTLE_SECONDS
            ),
            "warn"
    end

    return "waiting for the host's position", "warn"
end


function Diag.draw()

    if not Diag.visible then
        return
    end

    ImGui.SetNextWindowPos(20, 300, ImGuiCond.FirstUseEver)

    -- wersja w tytule (widać ją też po zwinięciu okna); "###" = stałe ID
    -- okna, więc pozycja zapisana przez ImGui nie zależy od wersji
    if not ImGui.Begin("CP2077 Coop v" .. Diag.VERSION .. "###CP2077Coop", ImGuiWindowFlags.AlwaysAutoResize) then

        ImGui.End()
        return
    end


    -- POŁĄCZENIE (stan z ostatniego odczytu DLL, jak w logu)
    local state =
        Diag.lastState

    local stateLevel = "bad"

    if state == "OK" then
        stateLevel = "good"
    elseif state == "STALE" or state == "WAITING" then
        stateLevel = "warn"
    elseif state == "PAUSED" then
        state = "PAUSED (you are in a menu / loading)"
        stateLevel = "neutral"
    end

    local hasRemote =
        Game.CP2077Coop_HasRemotePlayer ~= nil
        and Game.CP2077Coop_HasRemotePlayer()

    Diag.row("Version", Diag.VERSION, "neutral")
    Diag.row("Role", IS_HOST and "HOST" or "JOINER", "neutral")
    Diag.row("Connection", state, stateLevel)
    Diag.row("Players", hasRemote and "2 / 2" or "1 / 2", hasRemote and "good" or "warn")

    if Diag.roleConflict() then

        Diag.row(
            "Role check",
            IS_HOST and "BOTH HOST - one must be joiner"
                or "BOTH JOINER - one must be host",
            "bad"
        )
    end

    if Diag.peerLooksOutdated() then

        Diag.row("Peer", "no ping reply - other player on old version?", "warn")

        if Sync.remoteFlagsSeen and not Sync.peerHasRoleBit then
            Diag.row("Role check", "unknown - partner's mod too old to report its role", "warn")
        end
    end


    ImGui.Separator()

    -- OPÓŹNIENIE
    Diag.row("Player RTT", Diag.formatMs(Sync.rttMs) .. " ms", Diag.levelFor(Sync.rttMs, Diag.LIMITS.rtt_ms))
    Diag.row(
        "RTT min/max",
        Diag.formatMs(Sync.rttMinMs) .. " / " .. Diag.formatMs(Sync.rttMaxMs) .. " ms",
        "neutral"
    )

    local age =
        Diag.lastAge

    local ageLevel = "bad"

    if age ~= nil then
        ageLevel = Diag.levelFor(age * 1000.0, Diag.LIMITS.age_ms)
    end

    Diag.row(
        "Last packet",
        age and string.format("%.0f ms ago", age * 1000.0) or "never",
        ageLevel
    )

    -- czytamy raz na klatkę: więcej niż min(tempo nadawcy, fps) się nie da
    local readable =
        math.min(
            Diag.peerRate,
            Diag.fps
        )

    Diag.row(
        "Packets in",
        string.format(
            "%.1f read/s (partner sends %.0f/s, your fps %.0f)",
            Diag.ppsIn,
            Diag.peerRate,
            Diag.fps
        ),
        (readable > 0 and Diag.ppsIn >= 0.8 * readable) and "good" or "warn"
    )

    Diag.row(
        "Packets out",
        string.format(
            "%.1f/s, %s %% merged in one frame (your fps)",
            Diag.ppsOut,
            Diag.formatPct(Diag.mergedPctLast)
        ),
        (Diag.mergedPctLast or 0.0) < 10 and "neutral" or "warn"
    )

    local missed =
        Diag.missedPctLast

    Diag.row(
        "Missed",
        string.format(
            "%s %% last %.0f s (net loss / partner's DLL), session %.1f %%, late %d",
            Diag.formatPct(missed),
            Diag.STATS_INTERVAL,
            Diag.missedPercent(),
            Diag.ignored
        ),
        Diag.levelFor(missed, Diag.LIMITS.missed_pct)
    )

    Diag.row(
        "Overwritten",
        string.format(
            "%s %% (your fps below partner's rate - local, not the network)",
            Diag.formatPct(Diag.overwrittenPctLast)
        ),
        (Diag.overwrittenPctLast or 0.0) < 10 and "neutral" or "warn"
    )

    Diag.row(
        "Partner flags",
        string.format(
            "%s/s received (last %.0f s)",
            Diag.formatTenths(Diag.flagsInLast),
            Diag.STATS_INTERVAL
        ),
        "neutral"
    )

    Diag.row(
        "Frame time",
        string.format(
            "p99 %s ms (last %.0f s), %.0f fps",
            Diag.formatTenths(Diag.frameP99Last),
            Diag.STATS_INTERVAL,
            Diag.fps
        ),
        "neutral"
    )


    ImGui.Separator()

    -- SERWER (z coop_monitor.py)
    local monitor = Diag.monitor

    if monitor.server == nil then

        Diag.row("Relay server", "run: python coop-tools/coop_monitor.py", "warn")

    elseif monitor.stale then

        -- stary plik po zamkniętym monitorze: adres zostaje, ping nie
        Diag.row(
            "Relay server",
            monitor.server .. (
                monitor.ageSeconds ~= nil
                    and string.format(" (monitor stopped %.0f s ago)", monitor.ageSeconds)
                    or " (monitor not running)"
            ),
            "warn"
        )
    else

        Diag.row("Relay server", monitor.server, "neutral")
    end

    if monitor.server_ping_ms ~= nil
        and not monitor.stale
    then

        local serverPing =
            tonumber(monitor.server_ping_ms)

        Diag.row(
            "Your ping to relay",
            monitor.server_ping_ms .. " ms",
            Diag.levelFor(serverPing, Diag.LIMITS.server_ping_ms)
        )
    end

    if monitor.server_location ~= nil then
        Diag.row("Relay location", monitor.server_location, "neutral")
    end


    ImGui.Separator()

    -- AVATAR / STAN
    if not IS_HOST then

        local joinText, joinLevel =
            Diag.joinStatus()

        Diag.row("Join", joinText, joinLevel)
    end

    Diag.row(
        "Avatar",
        Diag.avatarState(),
        S.remoteHandle ~= nil and "good" or "warn"
    )

    if Diag.avatarError ~= nil then

        Diag.row(
            "Avatar drift",
            string.format(
                "%.2f m from where the partner is now (5 s avg %s)",
                Diag.avatarError,
                Diag.driftAvgLast and string.format("%.2f", Diag.driftAvgLast) or "-"
            ),
            Diag.levelFor(Diag.avatarError, Diag.LIMITS.avatar_err_m)
        )
    end

    -- cel fazy 3 (Total Sync Plan): najwyżej 1 na minutę
    Diag.row(
        "Hard corrections",
        string.format(
            "%s per min (avatar teleported to fix drift), %d this session",
            Diag.formatTenths(Diag.hardPerMinuteLast),
            Diag.hardTotal
        ),
        (Diag.hardPerMinuteLast or 0.0) <= 1.0 and "neutral" or "warn"
    )

    Diag.row("Remote speed", string.format("%.1f m/s (%s)", S.remoteSpeed or 0, S.movementType or "-"), "neutral")
    Diag.row("Remote state", Diag.describeFlags(Sync.remoteFlags), "neutral")

    if Sync.remoteVehicleIndex ~= nil then
        Diag.row("Remote vehicle", "#" .. tostring(Sync.remoteVehicleIndex) .. (Sync.vehicleShown and " (shown)" or " (spawning)"), Sync.vehicleShown and "good" or "warn")
    end
    Diag.row("Your state", Diag.describeFlags(Sync.localFlags), "neutral")

    -- porównanie modów
    local shared, onlyMine, onlyPartner, remoteCount =
        Mods.compare()

    if shared == nil then

        Diag.row(
            "Mods",
            string.format("you %d, partner list receiving...", #Mods.hashes),
            "neutral"
        )
    else

        local level = "good"

        if #onlyMine > 0 or onlyPartner > 0 then
            level = "warn"
        end

        Diag.row(
            "Mods",
            string.format("you %d, partner %d, shared %d", #Mods.hashes, remoteCount, shared),
            level
        )

        if onlyPartner > 0 then
            Diag.row("Only partner has", string.format("%d mod(s) you don't have", onlyPartner), "warn")
        end

        if #onlyMine > 0 then

            local shown = {}

            for index = 1, math.min(6, #onlyMine) do
                shown[#shown + 1] = onlyMine[index]
            end

            local more = #onlyMine - #shown

            Diag.row(
                "Only you have",
                table.concat(shown, ", ") .. (more > 0 and string.format(" (+%d more, see log)", more) or ""),
                "warn"
            )
        end
    end

    Diag.row(
        "Partner hits",
        string.format("%d received, %d applied, %d no match", Combat.hitsReceived, Combat.hitsApplied, Combat.hitsUnmatched),
        Combat.hitsUnmatched > Combat.hitsApplied and "warn" or "neutral"
    )

    Diag.row(
        "Scripts",
        Sync.scriptsReported and "redscript NOT compiled - no avatar" or "ok",
        Sync.scriptsReported and "bad" or "good"
    )


    ImGui.Separator()

    -- AKCJE (klikalne przy otwartym overlayu CET)
    if ImGui.Button(IS_HOST and "Switch to JOINER" or "Switch to HOST") then
        Diag.setRole(not IS_HOST)
    end

    if not IS_HOST then

        ImGui.SameLine()

        if ImGui.Button("Teleport to host") then
            Diag.teleportRequested = true
        end
    end

    ImGui.SameLine()

    if ImGui.Button("Log stats now") then
        Diag.writeStats(Diag.statsLine())
    end


    if ImGui.Button("Go to test area") then
        Diag.testAreaRequested = true
    end

    ImGui.SameLine()

    -- bot testowy: wysyła trasę zamiast prawdziwej pozycji
    if ImGui.Button(Bot.active and "Stop test pattern" or "Start test pattern") then
        Diag.botToggleRequested = true
    end

    if Bot.active then

        ImGui.SameLine()

        local r, g, b, a =
            Diag.colorFor("warn")

        ImGui.TextColored(
            r, g, b, a,
            string.format("sending test path: %s", Bot.phaseName())
        )
    end

    ImGui.End()
end


------------------------------------------------------------
-- REMOTE VEHICLE (co klatkę)
------------------------------------------------------------

function Sync.updateRemoteVehicle(player, delta)

    if player.CP2077Coop_ShowRemoteVehicle == nil
        or Sync.frozen ~= nil
    then
        return
    end

    -- stan z początku klatki (Diag.updateConnectionState, przed Sync.tick),
    -- jak panel i logi: po Sync.tick jedna długa lokalna klatka (przeciąganie
    -- okna, autozapis) liczyła się jak utrata połączenia i chowała auto,
    -- choć pakiety szły cały czas
    local lost =
        Diag.lastState == "LOST"

    local driving =
        not lost
        and Sync.hasFlag(
            Sync.remoteFlags,
            Sync.FLAG_IN_VEHICLE
        )

    if not driving then

        if Sync.vehicleShown
            or Sync.remoteVehicleIndex ~= nil
        then

            Sync.hideRemoteVehicle(player)

            if lost then
                Diag.log("[CP2077Coop] EVENT remote vehicle hidden: connection lost")
            end
        end

        return
    end

    if Sync.remoteVehicleIndex == nil
        or Sync.poseX == nil
    then
        return
    end


    local x, y, z, forwardX, forwardY, velX, velY, velZ, yawRate =
        Sync.extrapolateRemotePose()

    if Sync.carX == nil
        or distance3(
            Sync.carX,
            Sync.carY,
            Sync.carZ,
            x,
            y,
            z
        ) > Sync.VEHICLE_SNAP_DISTANCE
    then

        Sync.carX = x
        Sync.carY = y
        Sync.carZ = z
        Sync.carForwardX = forwardX
        Sync.carForwardY = forwardY

    else

        -- jedzie dalej z prędkością gracza, a skok celu przy nowym
        -- pakiecie (jitter sieci) wygładzamy zamiast pokazywać
        local blend =
            math.min(
                1.0,
                delta * Sync.VEHICLE_SMOOTHING
            )

        Sync.carX = Sync.carX + velX * delta
        Sync.carY = Sync.carY + velY * delta
        Sync.carZ = Sync.carZ + velZ * delta

        Sync.carX = Sync.carX + (x - Sync.carX) * blend
        Sync.carY = Sync.carY + (y - Sync.carY) * blend
        Sync.carZ = Sync.carZ + (z - Sync.carZ) * blend

        local headingX, headingY =
            Sync.rotate2(
                Sync.carForwardX,
                Sync.carForwardY,
                yawRate * delta
            )

        headingX = headingX + (forwardX - headingX) * blend
        headingY = headingY + (forwardY - headingY) * blend

        local headingLength =
            math.sqrt(
                headingX * headingX +
                headingY * headingY
            )

        if headingLength > 0.001 then

            Sync.carForwardX = headingX / headingLength
            Sync.carForwardY = headingY / headingLength
        end
    end

    Sync.vehicleShown =
        player:CP2077Coop_ShowRemoteVehicle(
            Sync.remoteVehicleIndex,
            Sync.carX,
            Sync.carY,
            Sync.carZ,
            Sync.carForwardX,
            Sync.carForwardY
        ) or Sync.vehicleShown
end


-- Drugi gracz jedzie pokazanym autem: avatar nie jedzie za nim
-- (teleport / AIMoveTo w karoserię = szarpanie auta i NPC).
-- Avatar czeka ukryty; po wyjściu z auta najpierw snap do gracza
-- (jak po spawnie), potem się pokazuje.
-- true = w tej klatce avatar stoi.
function Sync.parkAvatar(player)

    local driving =
        Sync.vehicleShown
        and Sync.hasFlag(
            Sync.remoteFlags,
            Sync.FLAG_IN_VEHICLE
        )

    if driving then

        if not S.avatarParked then

            S.avatarParked = true
            S.avatarUnhidePending = false
            S.spawnSnapPending = false

            cancelMoveCommand()
            Steer.reset()

            -- broń avatara to osobny obiekt (nie znika z avatarem);
            -- flagi wrócą w całości po wyjściu z auta
            if Sync.appliedFlags >= 0
                and Sync.hasScripts(player)
                and Sync.hasFlag(Sync.appliedFlags, Sync.FLAG_WEAPON_DRAWN)
            then
                player:CP2077Coop_ApplyRemoteWeapon(
                    Sync.weaponClass(Sync.appliedFlags),
                    false
                )
            end

            Sync.appliedFlags = -1
            Diag.avatarError = nil

            Sync.setAvatarVisible(player, false)

            Diag.log("[CP2077Coop] EVENT remote is driving: avatar parked")
        end

        return true
    end

    if S.avatarParked then

        S.avatarParked = false
        S.avatarUnhidePending = true

        S.lastCommandX = nil
        S.lastCommandY = nil
        S.lastCommandZ = nil

        S.spawnSnapPending = true
        S.spawnSnapElapsed = 0.0
        S.spawnSnapAccumulator = SPAWN_SNAP_INTERVAL
        S.spawnSnapFresh = false
        S.spawnGraceUntil = Sync.clock + Sync.SPAWN_GRACE

        return false
    end

    if S.avatarUnhidePending
        and not S.spawnSnapPending
    then

        S.avatarUnhidePending = false
        S.lastFacingX = nil
        S.lastFacingY = nil

        Sync.setAvatarVisible(player, true)
    end

    return false
end


------------------------------------------------------------
-- LOCAL PLAYER -> VPS
--
-- Odbiorca liczy czas z numerów sekwencji (numer = SEND_INTERVAL).
-- Wcześniej pakiet niósł pozycję z chwili klatki, a klatki nie
-- trafiają w siatkę 30 Hz (45 fps: odstępy 22/44 ms, 25 fps: 40 ms),
-- więc prędkość skakała o +-50%: sprint wyglądał jak dash (teleporty),
-- a po przycięciu gry jeden numer niósł ruch z wielu klatek.
-- Teraz pozycję czytamy co klatkę, a każde tyknięcie siatki 30 Hz
-- dostaje pozycję interpolowaną na swoją chwilę. Kilka tyknięć w
-- jednej klatce = kilka pushy; wątek DLL wysyła tylko ostatni, a
-- odbiorca liczy lukę w numerach jako upływ czasu.
------------------------------------------------------------

-- Pozycja, kierunek i źródło (zmiana źródła = skok, bez interpolacji).
function Sync.localPose(player)

    -- bot testowy podmienia pozycję i kierunek
    if Bot.active
        and Bot.anchor ~= nil
    then
        return
            { x = Bot.x, y = Bot.y, z = Bot.z, w = 1.0 },
            { x = Bot.forwardX, y = Bot.forwardY, z = 0.0 },
            "bot"
    end

    local pos =
        player:GetWorldPosition()

    local forward =
        player:GetWorldForward()

    -- w aucie: środek i kierunek auta, nie fotel gracza
    if Sync.hasFlag(
        Sync.realLocalFlags,
        Sync.FLAG_IN_VEHICLE
    ) then

        local carPos, carForward =
            Sync.mountedVehiclePose(
                player,
                pos,
                forward
            )

        if carPos ~= pos then
            return carPos, carForward, "vehicle"
        end
    end

    return pos, forward, "player"
end


function Sync.sendLocalState(player, delta)

    local pos, forward, source =
        Sync.localPose(player)

    S.sendAccumulator =
        S.sendAccumulator +
        delta

    local ticks =
        math.floor(
            S.sendAccumulator /
            SEND_INTERVAL
        )

    if ticks > 0 then

        -- długie przycięcie: tylko najnowsze tyknięcia
        if ticks > MAX_SEND_BACKLOG then

            S.sendAccumulator =
                S.sendAccumulator -
                (ticks - MAX_SEND_BACKLOG) *
                SEND_INTERVAL

            ticks = MAX_SEND_BACKLOG
        end

        -- kierunek w poziomie, długość dokładnie 1,
        -- zanim zakodujemy w niej stan gry
        local forwardLength =
            distance2(
                forward.x,
                forward.y,
                0.0,
                0.0
            )

        local flatX = 0.0
        local flatY = 1.0

        if forwardLength > 0.001 then

            flatX =
                forward.x /
                forwardLength

            flatY =
                forward.y /
                forwardLength
        end

        -- jeden stan gry na klatkę: każdy push w tej klatce niesie ten
        -- sam, więc ten, który DLL faktycznie wyśle, go dowiezie
        local sendX, sendY =
            Sync.encodeForward(
                flatX,
                flatY,
                Sync.buildPayload(
                    player,
                    IS_HOST
                )
            )

        local fromX = S.sendPrevX
        local fromY = S.sendPrevY
        local fromZ = S.sendPrevZ

        -- teleport, wczytanie gry, wejście do auta, start bota:
        -- bez pozycji pośrednich
        local smooth =
            fromX ~= nil
            and delta > 0.0
            and S.sendPrevSource == source
            and distance3(
                fromX,
                fromY,
                fromZ,
                pos.x,
                pos.y,
                pos.z
            ) <= math.max(
                TELEPORT_DISTANCE,
                Sync.VEHICLE_MAX_SPEED * delta
            )

        for _ = 1, ticks do

            S.sendAccumulator =
                S.sendAccumulator -
                SEND_INTERVAL

            local x = pos.x
            local y = pos.y
            local z = pos.z

            if smooth then

                -- tyknięcie było S.sendAccumulator s przed końcem klatki
                local alpha =
                    math.max(
                        0.0,
                        math.min(
                            1.0,
                            1.0 - S.sendAccumulator / delta
                        )
                    )

                x = fromX + (pos.x - fromX) * alpha
                y = fromY + (pos.y - fromY) * alpha
                z = fromZ + (pos.z - fromZ) * alpha
            end

            Game.CP2077Coop_PushPlayerState(
                x,
                y,
                z,
                pos.w,
                sendX,
                sendY
            )
        end

        Diag.onSent(ticks)
    end

    S.sendPrevX = pos.x
    S.sendPrevY = pos.y
    S.sendPrevZ = pos.z
    S.sendPrevSource = source
end


------------------------------------------------------------
-- TELEPORT REMOTE AVATAR
------------------------------------------------------------

-- isCorrection: teleport poprawia błąd (>= TELEPORT_DISTANCE, dojście
-- w bezruchu) - liczony w STATS hard_per_min. Podążanie teleportem przy
-- szybkim ruchu i snap po spawnie to nie korekty.
local function hardCorrectRemote(
    player,
    x,
    y,
    z,
    isCorrection
)

    -- bez redscriptu nie ma czym teleportować (i nie ma avatara)
    if player.CP2077Coop_MoveRemoteTest == nil then

        Sync.reportMissingScripts()
        return
    end

    if isCorrection then
        Diag.onHardCorrection()
    end

    cancelMoveCommand()

    -- po teleporcie stary cel ruchu jest nieaktualny
    Steer.reset()

    -- avatar po teleporcie patrzy jak gracz (wcześniej zawsze na
    -- północ: dash / skok / korekta obracały go do yaw 0)
    player:
        CP2077Coop_MoveRemoteTest(
            x,
            y,
            z,
            S.remoteForwardX,
            S.remoteForwardY
        )

    -- następny obrót w bezruchu wyrównuje avatar jeszcze raz
    -- (AI mogło zmienić obrót przed wykonaniem teleportu)
    S.lastFacingX = nil
    S.lastFacingY = nil
end


------------------------------------------------------------
-- TELEPORT REAL LOCAL PLAYER
------------------------------------------------------------

-- forwardX/forwardY: kierunek, w który gracz ma patrzeć po teleporcie
-- (nil = obecny kierunek gracza).
local function teleportLocalPlayer(
    player,
    x,
    y,
    z,
    forwardX,
    forwardY
)

    local ok, err =
        pcall(function()

            local position =
                Vector4.new(
                    x,
                    y,
                    z,
                    1.0
                )

            if forwardX == nil then

                local forward =
                    player:
                        GetWorldForward()

                forwardX = forward.x
                forwardY = forward.y
            end

            -- Teleport chce EulerAngles; Quaternion z GetWorldOrientation()
            -- kończył się błędem "parameter 3 must be EulerAngles"
            local rotation =
                EulerAngles.new(
                    0.0,
                    0.0,
                    Sync.yawFromForward(
                        forwardX,
                        forwardY
                    )
                )

            Game.GetTeleportationFacility():
                Teleport(
                    player,
                    position,
                    rotation
                )

        end)

    if not ok then

        Diag.log(
            "[CP2077Coop] PLAYER TELEPORT ERROR: "
            .. tostring(err)
        )

        return false
    end

    return true
end


------------------------------------------------------------
-- P2 -> HOST WORLD SYNC (teleport joinera do hosta)
--
-- Sync.updateJoin co klatkę: licznik spokojnej gry i postęp
-- wysłanej próby. Sync.beginJoinAttempt przy nowym pakiecie
-- hosta (świeża pozycja), gdy Sync.joinReady(). Przycisk
-- "Teleport to host" = Sync.restartJoin, ta sama ścieżka.
------------------------------------------------------------

-- Co trzyma gracza w miejscu (nil = nic). Prawdziwe flagi:
-- faza "vehicle" bota testowego nie blokuje.
function Sync.joinBlocker(player)

    if Sync.hasFlag(
        Sync.realLocalFlags,
        Sync.FLAG_IN_VEHICLE
    ) then
        return "in a vehicle"
    end

    -- bez redscriptu (albo stary state.reds) tieru nie znamy: jak bez sceny
    if player.CP2077Coop_GetSceneTier ~= nil
        and player:CP2077Coop_GetSceneTier() >=
            Sync.JOIN_SCENE_TIER
    then
        return "in a scene"
    end

    return nil
end


-- Gracz stoi w grze dość długo: kolejna próba rusza z nowym pakietem.
function Sync.joinReady()

    return
        not IS_HOST
        and not S.worldJoinComplete
        and S.joinPhase == "settle"
        and S.joinSettled >=
            Sync.JOIN_SETTLE_SECONDS
        and S.hostSettled >=
            Sync.JOIN_SETTLE_SECONDS
        and Sync.frozen == nil
end


-- Pakiet hosta (joiner, przed Sync.joinReady i przed zapisem
-- S.previousRemoteX): skok pozycji = host się wczytuje, robi szybką
-- podróż albo teleport. Cel teleportu z takiej pozycji byłby nieaktualny
-- (WORLD SYNC OK obok miejsca, gdzie hosta już nie ma), więc licznik
-- spokoju hosta od zera. Auto do VEHICLE_MAX_SPEED przez lukę w pakietach
-- nie jest skokiem.
function Sync.noteHostPacket(x, y, z, sequenceDelta, isRestart)

    local tick =
        SEND_INTERVAL *
        sequenceDelta

    local jump = 0.0

    if S.previousRemoteX ~= nil
        and not isRestart
    then

        jump =
            distance3(
                x, y, z,
                S.previousRemoteX,
                S.previousRemoteY,
                S.previousRemoteZ
            )
    end

    if isRestart
        or jump > Sync.JOIN_JUMP_DISTANCE + Sync.VEHICLE_MAX_SPEED * tick
    then

        S.hostSettled = 0.0
        S.hostJumpAt = Sync.clock

        if not S.worldJoinComplete
            and not isRestart
        then

            Diag.log(
                string.format(
                    "[CP2077Coop] WORLD SYNC waiting: host position jumped %.1f m (host loading or fast travelling), waiting for it to settle",
                    jump
                )
            )
        end

        return
    end

    S.hostSettled =
        S.hostSettled +
        tick
end


-- Avatar pojawia się przed lokalnym graczem, więc joiner prosi o spawn
-- dopiero po teleporcie do hosta (albo po poddaniu się). W aucie lub
-- scenie czekanie może trwać długo: wtedy spawn od razu, jak dawniej.
function Sync.joinAllowsSpawn()

    return
        IS_HOST
        or S.worldJoinComplete
        or (
            S.joinPhase == "settle"
            and S.joinWaitReason ~= nil
        )
end


-- Odległość gracza od miejsca, w którym stał przed teleportem tej próby.
function Sync.joinMoved(position)

    return
        distance3(
            position.x,
            position.y,
            position.z,
            S.joinStartX,
            S.joinStartY,
            S.joinStartZ
        )
end


function Sync.finishJoin(position, miss, note)

    S.worldJoinComplete = true
    S.joinPhase = "done"
    S.joinError = miss
    S.joinFailure = nil

    Diag.log(
        string.format(
            "[CP2077Coop] WORLD SYNC OK error=%.2f attempt=%d/%d moved=%.2f after=%.1fs%s",
            miss,
            S.joinAttempts,
            Sync.MAX_JOIN_ATTEMPTS,
            Sync.joinMoved(position),
            Sync.clock - S.joinCalledAt,
            note
        )
    )
end


-- Próba nieudana: zmierzony wynik do logu, potem przerwa albo koniec.
function Sync.failJoinAttempt(position, miss, reason)

    local moved =
        Sync.joinMoved(position)

    if reason == nil then

        if moved < Sync.JOIN_MOVED_EPSILON then
            reason = "position did not change"
        else
            reason = string.format(
                "landed %.2f m from the teleport point",
                miss
            )
        end
    end

    S.joinError = miss
    S.joinFailure = reason
    S.joinPhaseTime = 0.0

    local delays =
        Sync.JOIN_RETRY_DELAYS

    S.joinRetryDelay =
        delays[math.min(S.joinAttempts, #delays)]

    local giveUp =
        S.joinAttempts >=
        Sync.MAX_JOIN_ATTEMPTS

    Diag.log(
        string.format(
            "[CP2077Coop] WORLD SYNC FAILED error=%.2f attempt=%d/%d moved=%.2f waited=%.1fs: %s%s",
            miss,
            S.joinAttempts,
            Sync.MAX_JOIN_ATTEMPTS,
            moved,
            Sync.clock - S.joinCalledAt,
            reason,
            giveUp and ""
                or string.format(", next attempt in %.1f s", S.joinRetryDelay)
        )
    )

    if giveUp then

        -- Wcześniej: ponawianie w nieskończoność, co 2 s szarpało
        -- gracza i zamrażało avatar. Ręcznie: "Teleport to host".
        S.worldJoinComplete = true
        S.joinPhase = "gave_up"

        Diag.log(
            string.format(
                "[CP2077Coop] WORLD SYNC GAVE UP after %d attempts - use 'Teleport to host' in the coop panel",
                S.joinAttempts
            )
        )

        return
    end

    S.joinPhase = "retry"
end


-- Jedna próba: punkt obok hosta z TEGO pakietu i jeden teleport.
-- Wynik mierzy Sync.updateJoin w kolejnych klatkach (teleport
-- wykonuje się w grze z opóźnieniem).
function Sync.beginJoinAttempt(
    player,
    hostX,
    hostY,
    hostZ,
    hostForwardX,
    hostForwardY
)

    --------------------------------------------------------
    -- Perpendicular to host forward vector.
    -- Dzięki temu P2 ląduje ~1.75 m obok hosta.
    --------------------------------------------------------

    local sideX =
        -hostForwardY

    local sideY =
        hostForwardX

    local sideLength =
        math.sqrt(
            sideX * sideX +
            sideY * sideY
        )

    if sideLength < 0.001 then

        sideX = 1.0
        sideY = 0.0

    else

        sideX =
            sideX /
            sideLength

        sideY =
            sideY /
            sideLength
    end

    S.joinTargetX =
        hostX +
        sideX *
        JOIN_OFFSET

    S.joinTargetY =
        hostY +
        sideY *
        JOIN_OFFSET

    S.joinTargetZ =
        hostZ

    -- P2 patrzy w tę samą stronę co host
    S.joinForwardX = hostForwardX
    S.joinForwardY = hostForwardY

    local position =
        player:GetWorldPosition()

    S.joinStartX = position.x
    S.joinStartY = position.y
    S.joinStartZ = position.z

    S.joinAttempts =
        S.joinAttempts + 1

    S.joinPhase = "teleport"
    S.joinPhaseTime = 0.0
    S.joinCalledAt = Sync.clock
    S.joinPausedFor = 0.0

    local away =
        distance3(
            position.x,
            position.y,
            position.z,
            S.joinTargetX,
            S.joinTargetY,
            S.joinTargetZ
        )

    Diag.log(
        string.format(
            "[CP2077Coop] P2 WORLD SYNC -> %.2f %.2f %.2f attempt=%d/%d distance=%.2f settled=%.1fs since_load=%.1fs",
            S.joinTargetX,
            S.joinTargetY,
            S.joinTargetZ,
            S.joinAttempts,
            Sync.MAX_JOIN_ATTEMPTS,
            away,
            S.joinSettled,
            S.joinSinceLoad
        )
    )

    -- już na miejscu (np. zapis tuż obok hosta): bez teleportu
    if away <= Sync.JOIN_TOLERANCE then

        Sync.finishJoin(
            position,
            away,
            " (already there, no teleport)"
        )

        return
    end

    -- JEDEN teleport na próbę: kolejne wywołania przed wykonaniem
    -- pierwszego mogły go kasować (8 w 2 s na żywo, zero ruchu)
    if not teleportLocalPlayer(
        player,
        S.joinTargetX,
        S.joinTargetY,
        S.joinTargetZ,
        S.joinForwardX,
        S.joinForwardY
    ) then

        Sync.failJoinAttempt(
            position,
            away,
            "teleport call failed"
        )
    end
end


-- Co klatkę (joiner, gra wczytana): postęp próby, potem licznik
-- spokojnej gry. Licznik działa też po zakończeniu, żeby przycisk
-- "Teleport to host" ruszał od razu u gracza, który stoi w grze.
function Sync.updateJoin(player, delta)

    if IS_HOST then
        return
    end

    -- świat stoi (menu): czekanie na nasz teleport stoi razem z nim,
    -- przerwa przed kolejną próbą też; po menu znowu
    -- JOIN_SETTLE_SECONDS spokojnej gry (mapa = może szybka podróż)
    if Sync.frozen ~= nil then

        if not S.worldJoinComplete
            and S.joinPhase == "teleport"
        then
            S.joinPausedFor =
                S.joinPausedFor +
                delta
        end

        S.joinSettled = 0.0
        return
    end

    S.joinSinceLoad =
        S.joinSinceLoad +
        delta

    local position =
        player:GetWorldPosition()

    -- nasz teleport może właśnie lądować (także spóźniony, w przerwie)
    local phase = S.joinPhase

    local ownTeleport =
        not S.worldJoinComplete
        and (
            phase == "teleport"
            or phase == "retry"
        )

    if ownTeleport then

        -- wynik mierzony do punktu teleportu, nie do hosta, który
        -- w tym czasie idzie dalej
        local miss =
            distance3(
                position.x,
                position.y,
                position.z,
                S.joinTargetX,
                S.joinTargetY,
                S.joinTargetZ
            )

        if phase == "retry" then
            S.joinPhaseTime =
                S.joinPhaseTime +
                delta
        end

        -- host skoczył po wzięciu celu: punkt jest nieaktualny, choć
        -- wylądowaliśmy na nim (próba się liczy, kolejna celuje w nowe
        -- miejsce, gdy host się uspokoi)
        local hostJumped =
            S.hostJumpAt >= S.joinCalledAt

        if miss <= Sync.JOIN_TOLERANCE
            and not hostJumped
        then

            Sync.finishJoin(
                position,
                miss,
                phase == "retry"
                    and " (previous teleport applied late)"
                    or ""
            )

        elseif miss <= Sync.JOIN_TOLERANCE
            and phase == "teleport"
        then

            Sync.failJoinAttempt(
                position,
                miss,
                "the host jumped during the attempt (fast travel or still loading)"
            )

        elseif phase == "teleport"
            and Sync.clock - S.joinCalledAt - S.joinPausedFor >=
                Sync.JOIN_APPLY_TIMEOUT
        then

            Sync.failJoinAttempt(
                position,
                miss,
                nil
            )

        elseif phase == "retry"
            and S.joinPhaseTime >=
                S.joinRetryDelay
        then

            S.joinPhase = "settle"
        end
    end


    -- auto / scena: licznik od zera
    local reason =
        Sync.joinBlocker(player)

    if reason ~= S.joinWaitReason then

        S.joinWaitReason = reason

        if reason ~= nil
            and not S.worldJoinComplete
        then
            Diag.log("[CP2077Coop] WORLD SYNC waiting: you are " .. reason)
        end
    end

    -- skok pozycji nie przez nasz teleport: gra jeszcze ustawia
    -- gracza (ładowanie, szybka podróż), licznik od zera
    local jump = 0.0

    if S.joinLastX ~= nil
        and not ownTeleport
    then

        jump =
            distance3(
                position.x,
                position.y,
                position.z,
                S.joinLastX,
                S.joinLastY,
                S.joinLastZ
            )
    end

    S.joinLastX = position.x
    S.joinLastY = position.y
    S.joinLastZ = position.z

    if reason ~= nil then

        S.joinSettled = 0.0

    elseif jump > Sync.JOIN_JUMP_DISTANCE then

        S.joinSettled = 0.0

        if not S.worldJoinComplete then

            Diag.log(
                string.format(
                    "[CP2077Coop] WORLD SYNC waiting: position jumped %.1f m (game still placing you), settling again",
                    jump
                )
            )
        end

    else

        S.joinSettled =
            S.joinSettled +
            delta
    end
end


-- Przycisk "Teleport to host": ta sama ścieżka od nowa (pełne próby).
function Sync.restartJoin()

    if IS_HOST then
        return
    end

    if not S.worldJoinComplete
        and S.joinPhase == "teleport"
    then

        Diag.log("[CP2077Coop] EVENT manual teleport to host requested - a teleport is already running")
        return
    end

    S.worldJoinComplete = false
    S.joinAttempts = 0
    S.joinPhase = "settle"
    S.joinPhaseTime = 0.0

    Diag.log("[CP2077Coop] EVENT manual teleport to host requested")
end


------------------------------------------------------------
-- RESET LOCAL SCRIPT SESSION
------------------------------------------------------------

local function resetRemote()

    cancelMoveCommand()

    -- auto drugiego gracza i ukryty avatar nie mogą zostać w świecie
    -- (Sync.reset tylko zapomina o nich)
    local player =
        Game.GetPlayer()

    if player ~= nil then

        Sync.hideRemoteVehicle(player)

        if S.avatarParked or S.avatarUnhidePending then
            Sync.setAvatarVisible(player, true)
        end
    end

    S.avatarParked = false
    S.avatarUnhidePending = false

    S.remoteInitialized = false
    S.remoteHandle = nil

    S.spawnRequestedAt = nil
    S.spawnAttempts = 0
    S.spawnDeferredLogged = false
    S.handlePollAccumulator = 0.0

    S.lastRemoteSequence = -1

    -- DLL nie czyści HasRemotePlayer i trzyma ostatni pakiet na zawsze:
    -- to, co leży w slocie teraz (np. sprzed wyjścia drugiego gracza),
    -- ignorujemy, aż przyjdzie pakiet z innym numerem
    S.staleSequence = nil

    if Game.CP2077Coop_HasRemotePlayer() then

        S.staleSequence =
            Game.CP2077Coop_GetRemoteSequence()
    end

    S.olderStreak = 0
    S.lastIgnoredSequence = nil

    S.lastMoveSequence = nil
    S.combatSinceMove = 0
    S.moveTicks = 0

    S.previousRemoteX = nil
    S.previousRemoteY = nil
    S.previousRemoteZ = nil

    S.remoteVelocityX = 0.0
    S.remoteVelocityY = 0.0
    S.remoteVelocityZ = 0.0

    S.remoteSpeed = 0.0
    S.remoteVerticalJump = false

    S.settleAccumulator = IDLE_SETTLE_INTERVAL

    S.targetX = 0.0
    S.targetY = 0.0
    S.targetZ = 0.0

    S.remoteForwardX = 0.0
    S.remoteForwardY = 1.0

    S.lastFacingX = nil
    S.lastFacingY = nil

    S.remoteMoving = false
    S.movementType = "Walk"

    S.idleTimer = 0.0

    S.lastCommandX = nil
    S.lastCommandY = nil
    S.lastCommandZ = nil

    S.spawnSnapPending = false
    S.spawnSnapElapsed = 0.0
    S.spawnSnapAccumulator = 0.0
    S.spawnSnapFresh = false
    S.spawnPlaceWait = 0.0
    S.spawnGraceUntil = -100.0

    -- wczytanie gry / zmiana roli: teleport do hosta od nowa, licznik
    -- spokojnej gry od zera
    S.joinPhase = "settle"
    S.joinAttempts = 0
    S.joinPhaseTime = 0.0
    S.joinRetryDelay = 0.0
    S.joinCalledAt = 0.0
    S.joinSettled = 0.0
    S.joinSinceLoad = 0.0
    S.joinWaitReason = nil
    S.joinLastX = nil
    S.joinLastY = nil
    S.joinLastZ = nil
    S.joinError = nil
    S.joinFailure = nil
    S.hostSettled = 0.0
    S.hostJumpAt = -100.0
    S.joinPausedFor = 0.0

    S.worldJoinComplete = IS_HOST

    S.joinTargetX = 0.0
    S.joinTargetY = 0.0
    S.joinTargetZ = 0.0
    S.joinForwardX = nil
    S.joinForwardY = nil

    S.sendAccumulator = 0.0
    S.commandAccumulator = 0.0
    S.rotateAccumulator = 0.0

    S.sendPrevX = nil
    S.sendPrevY = nil
    S.sendPrevZ = nil
    S.sendPrevSource = nil

    S.rolePrinted = false

    -- bot testowy: nowy środek trasy tam, gdzie gracz jest po wczytaniu
    Bot.anchor = nil

    Sync.reset()
    Diag.resetSession()
    Steer.reset()
    Steer.resetHardCorrect()
    Steer.resetSettle()
end


------------------------------------------------------------
-- INIT
------------------------------------------------------------

registerForEvent(
    "onInit",
    function()

        Diag.loadRole()
        Mods.load()

        Diag.log(
            "[CP2077Coop] bridge v" .. Diag.VERSION .. " loaded, role="
            .. (IS_HOST and "host" or "joiner")
        )

    end
)


------------------------------------------------------------
-- PANEL
------------------------------------------------------------

registerForEvent(
    "onDraw",
    function()

        Diag.draw()

    end
)


registerHotkey(
    "cp2077coop_toggle_panel",
    "Toggle coop panel",
    function()

        Diag.visible =
            not Diag.visible

    end
)


------------------------------------------------------------
-- UPDATE
------------------------------------------------------------

registerForEvent(
    "onUpdate",
    function(delta)


        ----------------------------------------------------
        -- CLOCK / DIAGNOSTICS
        ----------------------------------------------------

        Diag.updateConnectionState()
        Sync.tick(delta)
        Diag.tick(delta)


        -- zmiana roli z panelu: start sesji od nowa
        if Diag.roleChanged then

            Diag.roleChanged = false
            resetRemote()
        end


        -- przycisk "Teleport to host" w panelu: ta sama ścieżka co
        -- teleport po wczytaniu (Sync.updateJoin)
        if Diag.teleportRequested then

            Diag.teleportRequested = false

            Sync.restartJoin()
        end


        ----------------------------------------------------
        -- GAMEPLAY STATE
        ----------------------------------------------------

        local loaded =
            isGameplayLoaded()


        if loaded ~= S.syncActive then

            S.syncActive = loaded


            if S.syncActive then

                Diag.log(
                    "[CP2077Coop] sync ON"
                )

                resetRemote()

            else

                Diag.log(
                    "[CP2077Coop] sync OFF"
                )

                resetRemote()

            end
        end


        if not S.syncActive then
            return
        end


        local player =
            Game.GetPlayer()

        if player == nil then
            return
        end

        -- menu / mapa / ekwipunek: świat stoi (Sync.frozen)
        Sync.updateFrozen(delta)


        ----------------------------------------------------
        -- ROLE
        ----------------------------------------------------

        if not S.rolePrinted then

            S.rolePrinted = true

            if IS_HOST then

                Diag.log(
                    "[CP2077Coop] ROLE = HOST"
                )

            else

                Diag.log(
                    "[CP2077Coop] ROLE = JOINER"
                )
            end
        end


        ----------------------------------------------------
        -- TEST PATTERN BOT
        ----------------------------------------------------

        if Diag.botToggleRequested then

            Diag.botToggleRequested = false

            if Bot.active then
                Bot.stop()
            else
                Bot.start()
            end
        end

        -- przycisk "Go to test area" w panelu
        if Diag.testAreaRequested then

            Diag.testAreaRequested = false

            local area = Diag.TEST_AREA

            if teleportLocalPlayer(player, area.x, area.y, area.z) then

                Diag.log("[CP2077Coop] EVENT teleported to test area: " .. area.name)

                -- trasa bota przy celu (teleport kończy się dopiero
                -- w kolejnych klatkach, pozycja gracza jest jeszcze stara)
                if Bot.anchor ~= nil then

                    Bot.anchor = {
                        x = area.x + Bot.CENTER_OFFSET,
                        y = area.y,
                        z = area.z
                    }
                end
            end
        end

        -- plik testpattern.txt = start bota po załadowaniu gry
        if not S.botFileChecked then

            S.botFileChecked = true

            local botFile =
                io.open(Bot.FILE, "r")

            if botFile ~= nil then

                botFile:close()
                Bot.start()
            end
        end

        Bot.update(
            delta,
            player
        )


        ----------------------------------------------------
        -- LOCAL PLAYER -> VPS (siatka 30 Hz, patrz Sync.sendLocalState)
        ----------------------------------------------------

        Sync.sendLocalState(
            player,
            delta
        )


        ----------------------------------------------------
        -- REMOTE VEHICLE: pozycja "teraz", też bez nowego
        -- pakietu; znika przy utracie połączenia
        ----------------------------------------------------

        Sync.updateRemoteVehicle(
            player,
            delta
        )


        ----------------------------------------------------
        -- HOST TIME / WEATHER: co klatkę, niezależnie od avatara
        -- (spawn, teleport do hosta, snap, auto). Wcześniej
        -- działało dopiero przy gotowym avatarze, więc nieudany
        -- spawn = joiner nigdy nie dostawał godziny i pogody.
        ----------------------------------------------------

        Sync.applyWorldState(
            player,
            IS_HOST,
            delta
        )


        ----------------------------------------------------
        -- JOINER -> HOST: licznik spokojnej gry i postęp
        -- teleportu co klatkę, też bez nowego pakietu
        ----------------------------------------------------

        Sync.updateJoin(
            player,
            delta
        )


        ----------------------------------------------------
        -- NO FRESH REMOTE DATA
        ----------------------------------------------------

        if not Game.CP2077Coop_HasRemotePlayer() then
            return
        end


        ----------------------------------------------------
        -- NEW REMOTE NETWORK STATE
        ----------------------------------------------------

        Diag.onPoll()

        local sequence =
            Game.CP2077Coop_GetRemoteSequence()


        ----------------------------------------------------
        -- UDP: tylko nowsze pakiety. Spóźniony stary pakiet
        -- cofałby postać. Restart drugiej gry = numery od 1.
        ----------------------------------------------------

        -- pakiet sprzed resetu (wczytanie gry, zmiana roli): jak brak
        -- danych, dopóki w slocie nie pojawi się inny numer
        local isStale =
            sequence == S.staleSequence

        if not isStale then
            S.staleSequence = nil
        end

        local isNewer =
            not isStale
            and sequence > S.lastRemoteSequence

        -- duży spadek albo starszy numer po ciszy: start gry trwa
        -- długo, a spóźniony pakiet z żywego strumienia nigdy nie
        -- przychodzi po STALE_AFTER. Ten sam numer co ostatnio
        -- pominięty = pakiet, który DLL wciąż trzyma, nie nowy.
        local isRestart =
            not isStale
            and sequence < S.lastRemoteSequence
            and (
                sequence < S.lastRemoteSequence - SEQUENCE_RESET_GAP
                or (
                    sequence ~= S.lastIgnoredSequence
                    and (Diag.packetAge() or 0.0) >= Diag.STALE_AFTER
                )
            )

        -- spóźniony pakiet: liczymy raz (DLL trzyma go aż do następnego)
        if not isNewer
            and not isRestart
            and sequence < S.lastRemoteSequence
            and sequence ~= S.lastIgnoredSequence
        then

            S.lastIgnoredSequence = sequence

            -- spóźnione pakiety UDP przychodzą pojedynczo; seria
            -- różnych starszych numerów = druga gra liczy od nowa
            S.olderStreak =
                S.olderStreak + 1

            if S.olderStreak >=
                Diag.RESTART_OLDER_STREAK
            then
                isRestart = true
            else
                Diag.onIgnored()
            end
        end


        -- cały pakiet ze slotu DLL. Wątek sieci zapisuje x..fy, potem
        -- numer, bez blokady: inny numer po odczycie = pola mogą być
        -- z dwóch pakietów (zły stan gry, skok prędkości). Taki odczyt
        -- pomijamy, nowszy pakiet weźmiemy w całości w następnej klatce.
        local rx, ry, rz, rawForwardX, rawForwardY

        if isNewer
            or isRestart
        then

            rx = Game.CP2077Coop_GetRemoteX()
            ry = Game.CP2077Coop_GetRemoteY()
            rz = Game.CP2077Coop_GetRemoteZ()

            rawForwardX = Game.CP2077Coop_GetRemoteForwardX()
            rawForwardY = Game.CP2077Coop_GetRemoteForwardY()

            if Game.CP2077Coop_GetRemoteSequence() ~= sequence then

                isNewer = false
                isRestart = false

                Diag.tornReads =
                    Diag.tornReads + 1
            end
        end


        if isNewer
            or isRestart
        then

            S.olderStreak = 0

            local previousSequence =
                S.lastRemoteSequence

            if isRestart then

                Diag.onPacket(sequence, -1)

                Diag.log(
                    string.format(
                        "[CP2077Coop] EVENT peer restart detected: sequence %d after %d",
                        sequence,
                        previousSequence
                    )
                )
            else
                Diag.onPacket(sequence, previousSequence)
            end

            S.lastRemoteSequence =
                sequence


            -- pakiet bojowy (combat.reds): x/y/z to pozycja NPC, nie gracza.
            -- Nie dekodujemy stanu i nie ruszamy avatara w tej klatce.
            if Combat.isPacket(rawForwardY) then

                Combat.applyRemoteHit(
                    player,
                    rx, ry, rz,
                    rawForwardX
                )

                -- ten numer nie był tyknięciem ruchu
                S.combatSinceMove =
                    S.combatSinceMove + 1

                return
            end


            -- kierunek + zakodowany stan gry (patrz GAMEPLAY / WORLD STATE SYNC)
            local forwardX, forwardY, payload =
                Sync.decodeForward(
                    rawForwardX,
                    rawForwardY
                )

            S.remoteForwardX = forwardX
            S.remoteForwardY = forwardY

            -- restart drugiego klienta: stare auto znika, pokaże się
            -- znowu po pakiecie z modelem pojazdu
            if isRestart
                and (Sync.vehicleShown or Sync.remoteVehicleIndex ~= nil)
            then
                Sync.hideRemoteVehicle(player)
            end

            if isRestart then
                Sync.forgetPeerVersion()
            end

            Sync.receivePayload(payload)


            -- czas od poprzedniego pakietu RUCHU w tyknięciach 30 Hz.
            -- Luka w numerach (zgubione w sieci, nadpisane w DLL, kilka
            -- tyknięć w jednej klatce nadawcy) to upływ czasu; numery
            -- odebranych pakietów bojowych nie.
            local sequenceDelta = 1

            if S.lastMoveSequence ~= nil
                and not isRestart
            then

                sequenceDelta =
                    math.max(
                        1,
                        sequence -
                        S.lastMoveSequence -
                        S.combatSinceMove
                    )
            end

            S.lastMoveSequence = sequence
            S.combatSinceMove = 0

            S.moveTicks =
                S.moveTicks +
                sequenceDelta

            -- nowa sesja drugiego gracza: stara historia pozy nie pasuje
            if isRestart then
                Sync.poseHistory = {}
            end

            -- auto rysuje Sync.updateRemoteVehicle co klatkę
            Sync.recordRemotePose(
                S.moveTicks,
                rx, ry, rz,
                forwardX, forwardY
            )


            ------------------------------------------------
            -- P2 JOINS P1 WORLD
            --
            -- próba dopiero, gdy gracz stoi w grze dość długo
            -- (nie w aucie, nie w scenie: Sync.updateJoin),
            -- z pozycją hosta z tego pakietu
            ------------------------------------------------

            if not IS_HOST then
                Sync.noteHostPacket(rx, ry, rz, sequenceDelta, isRestart)
            end

            if Sync.joinReady() then

                Sync.beginJoinAttempt(
                    player,
                    rx,
                    ry,
                    rz,
                    S.remoteForwardX,
                    S.remoteForwardY
                )
            end


            ------------------------------------------------
            -- MOVEMENT / VELOCITY
            ------------------------------------------------

            if S.previousRemoteX ~= nil then

                local packetTime =
                    SEND_INTERVAL *
                    sequenceDelta

                packetTime =
                    math.max(
                        packetTime,
                        0.001
                    )


                local dx =
                    rx -
                    S.previousRemoteX

                local dy =
                    ry -
                    S.previousRemoteY

                local dz =
                    rz -
                    S.previousRemoteZ


                local packetDistance =
                    math.sqrt(
                        dx * dx +
                        dy * dy +
                        dz * dz
                    )


                local speed =
                    packetDistance /
                    packetTime

                S.remoteSpeed =
                    speed

                -- skok / spadek: pozioma prędkość nie wystarczy
                -- (na jedno tyknięcie: luka w numerach to dłuższy czas)
                S.remoteVerticalJump =
                    math.abs(dz) / sequenceDelta >
                    VERTICAL_SNAP *
                    0.5


                if packetDistance >
                    MOVEMENT_EPSILON *
                    sequenceDelta
                then

                    S.remoteMoving = true
                    S.idleTimer = 0.0

                    S.remoteVelocityX =
                        dx /
                        packetTime

                    S.remoteVelocityY =
                        dy /
                        packetTime

                    S.remoteVelocityZ =
                        dz /
                        packetTime


                    if speed >=
                        SPRINT_SPEED
                    then

                        S.movementType =
                            "Sprint"

                    elseif speed >=
                        RUN_SPEED
                    then

                        S.movementType =
                            "Run"

                    else

                        S.movementType =
                            "Walk"

                    end

                else

                    S.idleTimer =
                        S.idleTimer +
                        packetTime

                    S.remoteVelocityX = 0.0
                    S.remoteVelocityY = 0.0
                    S.remoteVelocityZ = 0.0

                    if S.idleTimer >=
                        IDLE_DELAY
                    then

                        S.remoteMoving = false
                        S.remoteSpeed = 0.0
                    end
                end

            else

                S.remoteVelocityX = 0.0
                S.remoteVelocityY = 0.0
                S.remoteVelocityZ = 0.0
            end


            S.previousRemoteX = rx
            S.previousRemoteY = ry
            S.previousRemoteZ = rz


            ------------------------------------------------
            -- ABSOLUTE WORLD SYNC
            --
            -- Koniec relative origin.
            -- Remote używa tych samych world coordinates.
            ------------------------------------------------

            -- bez predykcji przy dashu / pojeździe: przy nagłym
            -- zatrzymaniu postać przeskakiwała do przodu
            if S.remoteMoving
                and S.remoteSpeed < FAST_SPEED
            then

                local leadX =
                    S.remoteVelocityX *
                    PREDICTION_TIME

                local leadY =
                    S.remoteVelocityY *
                    PREDICTION_TIME

                local leadLength =
                    distance2(
                        leadX,
                        leadY,
                        0.0,
                        0.0
                    )

                if leadLength >
                    MAX_PREDICTION_DISTANCE
                then

                    leadX =
                        leadX *
                        MAX_PREDICTION_DISTANCE /
                        leadLength

                    leadY =
                        leadY *
                        MAX_PREDICTION_DISTANCE /
                        leadLength
                end

                S.targetX =
                    rx +
                    leadX

                S.targetY =
                    ry +
                    leadY

                -- Nie przewidujemy Z, żeby nie podbijać NPC
                -- na schodach / spadkach.
                S.targetZ =
                    rz

            else

                S.targetX = rx
                S.targetY = ry
                S.targetZ = rz
            end


            ------------------------------------------------
            -- SPAWN REMOTE AVATAR ON FIRST PACKET
            ------------------------------------------------

            -- joiner przed teleportem do hosta: spawn dopiero po nim
            -- (avatar pojawia się przed lokalnym graczem, nie w miejscu,
            -- które gracz zaraz opuści; patrz Sync.joinAllowsSpawn)
            if not S.remoteInitialized
                and Sync.joinAllowsSpawn()
                and Sync.frozen == nil
            then

                Sync.requestSpawn(
                    player
                )
            end
        end


        ----------------------------------------------------
        -- P2 REAL PLAYER WORLD-SYNC TELEPORT
        --
        -- teleport w drodze (Sync.updateJoin mierzy wynik):
        -- w tym ticku nie ruszamy jeszcze remote AI
        ----------------------------------------------------

        if not S.worldJoinComplete
            and S.joinPhase == "teleport"
        then
            return
        end

        -- świat stoi (menu): bez szukania avatara, snapu, korekt i
        -- komend ruchu; cel i prędkość idą dalej z pakietów, więc po
        -- menu jedna zwykła korekta dogania drugiego gracza
        if Sync.frozen ~= nil then
            return
        end


        ----------------------------------------------------
        -- WAIT FOR REMOTE NPC HANDLE
        ----------------------------------------------------

        if S.remoteInitialized
            and S.remoteHandle == nil
        then

            S.handlePollAccumulator =
                S.handlePollAccumulator +
                delta

            if S.handlePollAccumulator >=
                Sync.HANDLE_POLL_INTERVAL
            then

                S.handlePollAccumulator = 0.0

                S.remoteHandle =
                    getRemoteHandle()

                if S.remoteHandle == nil then
                    Sync.checkSpawnTimeout(player)
                end
            end


            if S.remoteHandle ~= nil then

                Diag.log(
                    "[CP2077Coop] remote entity acquired"
                )

                -- nowy avatar: kucanie i broń od nowa
                Sync.appliedFlags = -1

                local playerPos =
                    player:
                        GetWorldPosition()

                local remotePos =
                    S.remoteHandle:
                        GetWorldPosition()


                Diag.log(
                    string.format(
                        "[CP2077Coop] LOCAL %.2f %.2f %.2f",
                        playerPos.x,
                        playerPos.y,
                        playerPos.z
                    )
                )

                Diag.log(
                    string.format(
                        "[CP2077Coop] REMOTE TARGET %.2f %.2f %.2f",
                        S.targetX,
                        S.targetY,
                        S.targetZ
                    )
                )

                Diag.log(
                    string.format(
                        "[CP2077Coop] JUDY BEFORE SNAP %.2f %.2f %.2f",
                        remotePos.x,
                        remotePos.y,
                        remotePos.z
                    )
                )


                -- nowy avatar stoi zwykle już przy drugim graczu
                -- (Sync.spawnPoint): snap czeka, aż gra go ustawi, i
                -- teleportuje dopiero od TELEPORT_DISTANCE
                S.spawnSnapPending = true
                S.spawnSnapElapsed = 0.0
                S.spawnSnapAccumulator =
                    Steer.HARD_CORRECT_COOLDOWN
                S.spawnSnapFresh = true
                S.spawnPlaceWait = 0.0
                S.spawnGraceUntil = Sync.clock + Sync.SPAWN_GRACE
                Steer.resetHardCorrect()


                S.lastCommandX = S.targetX
                S.lastCommandY = S.targetY
                S.lastCommandZ = S.targetZ
            end
        end


        if S.remoteHandle == nil then
            return
        end


        ----------------------------------------------------
        -- REMOTE DRIVES A SHOWN CAR
        ----------------------------------------------------

        -- avatar czeka ukryty
        if Sync.parkAvatar(player) then
            return
        end


        ----------------------------------------------------
        -- FORCE INITIAL REMOTE POSITION
        ----------------------------------------------------

        if S.spawnSnapPending then

            local snapPos =
                S.remoteHandle:
                    GetWorldPosition()

            -- nowa encja, której gra jeszcze nie ustawiła (0, 0, 0):
            -- AITeleportCommand czekałby na AI, kolejne by się
            -- nadpisywały - nic nie wysyłamy i nie liczymy czasu snapu
            if S.spawnSnapFresh
                and math.abs(snapPos.x) + math.abs(snapPos.y) + math.abs(snapPos.z) < 0.01
                and S.spawnPlaceWait < Sync.SPAWN_PLACE_WAIT
            then

                S.spawnPlaceWait =
                    S.spawnPlaceWait +
                    delta

                return
            end

            S.spawnSnapElapsed =
                S.spawnSnapElapsed +
                delta

            S.spawnSnapAccumulator =
                S.spawnSnapAccumulator +
                delta


            local snapError =
                distance3(
                    snapPos.x,
                    snapPos.y,
                    snapPos.z,

                    S.targetX,
                    S.targetY,
                    S.targetZ
                )

            -- nowy avatar bliżej niż TELEPORT_DISTANCE: resztę dojdzie
            -- AIMoveTo; teleport tylko z daleka, najwyżej co
            -- HARD_CORRECT_COOLDOWN (po wyjściu z auta jak dawniej)
            local placed =
                snapError <= SPAWN_SNAP_TOLERANCE
                or (S.spawnSnapFresh and snapError < TELEPORT_DISTANCE)

            local snapInterval =
                S.spawnSnapFresh
                and Steer.HARD_CORRECT_COOLDOWN
                or SPAWN_SNAP_INTERVAL

            if not placed
                and S.spawnSnapElapsed < SPAWN_SNAP_DURATION
                and S.spawnSnapAccumulator >= snapInterval
            then

                S.spawnSnapAccumulator = 0.0

                hardCorrectRemote(
                    player,
                    S.targetX,
                    S.targetY,
                    S.targetZ
                )
            end


            if placed then

                S.spawnSnapPending = false

                Diag.log(
                    string.format(
                        "[CP2077Coop] REMOTE SNAP OK error=%.2f",
                        snapError
                    )
                )

            elseif S.spawnSnapElapsed >=
                SPAWN_SNAP_DURATION
            then

                S.spawnSnapPending = false

                Diag.log(
                    string.format(
                        "[CP2077Coop] REMOTE SNAP FAILED error=%.2f target=%.2f %.2f %.2f",
                        snapError,
                        S.targetX,
                        S.targetY,
                        S.targetZ
                    )
                )
            end

            return
        end


        ----------------------------------------------------
        -- REMOTE POSITION ERROR
        ----------------------------------------------------

        -- stan gracza (kucanie, broń); czas i pogoda: wyżej, co klatkę
        Sync.applyRemoteFlags(player)

        local current =
            S.remoteHandle:
                GetWorldPosition()


        local errorDistance =
            distance3(
                current.x,
                current.y,
                current.z,

                S.targetX,
                S.targetY,
                S.targetZ
            )

        Diag.recordDrift(current)


        ----------------------------------------------------
        -- LARGE DESYNC
        ----------------------------------------------------

        ----------------------------------------------------
        -- DASH / JUMP / VEHICLE FOLLOW
        --
        -- AIMoveTo nie odtworzy uniku, skoku na dach ani
        -- jazdy autem - NPC zostaje w tyle i potem skacze
        -- o 6 m. Wtedy podążamy teleportem przy każdej
        -- zmianie celu (30 Hz), co wygląda płynniej.
        ----------------------------------------------------

        local verticalError =
            math.abs(
                current.z -
                S.targetZ
            )

        local snapFollow =
            S.remoteMoving
            and (
                S.remoteSpeed >= FAST_SPEED
                or S.remoteVerticalJump
                or verticalError >= VERTICAL_SNAP
            )

        local targetMoved =
            S.lastCommandX == nil
            or distance3(
                S.targetX,
                S.targetY,
                S.targetZ,
                S.lastCommandX,
                S.lastCommandY,
                S.lastCommandZ
            ) >= MIN_TARGET_CHANGE

        local snapNow =
            snapFollow
            and targetMoved

        if snapNow
            or errorDistance >= TELEPORT_DISTANCE
        then

            -- teleport jeszcze w drodze: żadnej nowej komendy
            -- (kolejny teleport albo AIMoveTo by go nadpisały)
            if not Steer.hardCorrectAllowed(
                current,
                errorDistance,
                snapNow
            ) then
                return
            end

            hardCorrectRemote(
                player,
                S.targetX,
                S.targetY,
                S.targetZ,
                not snapNow
                    and Sync.clock >= S.spawnGraceUntil
            )

            S.lastCommandX = S.targetX
            S.lastCommandY = S.targetY
            S.lastCommandZ = S.targetZ

            return
        end

        -- Szybki ruch: tylko teleport przy nowym pakiecie. AIMoveTo
        -- wysłane w następnej klatce nadpisywało teleport i avatar
        -- zostawał 14-16 m w tyle (test na żywo, faza "vehicle").
        if snapFollow then
            return
        end


        ----------------------------------------------------
        -- REMOTE MOVING
        ----------------------------------------------------

        if S.remoteMoving then

            -- po zatrzymaniu: nowa seria dojścia, pierwsza próba od razu
            Steer.resetSettle()
            S.settleAccumulator = IDLE_SETTLE_INTERVAL

            -- Steer: rzadkie, "lepkie" komendy ruchu zamiast
            -- anulowania i wysyłania nowej co 0.12 s (AI stawało).
            local moveType =
                Sync.catchUpMoveType(
                    S.movementType,
                    errorDistance
                )

            local crouched =
                Sync.isRemoteCrouching()

            local endX, endY, endZ =
                Steer.endpoint()

            if Steer.shouldReissue(
                current,
                endX,
                endY,
                endZ,
                moveType,
                crouched,
                delta
            ) then

                if moveRemoteAI(
                    endX,
                    endY,
                    endZ,
                    moveType,
                    crouched
                ) then

                    -- S.lastCommand* zostaje "ostatni teleport / cel
                    -- korekty" (bramka targetMoved); punkt Steer
                    -- pamięta Steer.remember
                    Steer.remember(
                        endX,
                        endY,
                        endZ,
                        moveType,
                        crouched
                    )
                end
            end


        ----------------------------------------------------
        -- REMOTE IDLE
        ----------------------------------------------------

        else

            Steer.reset()


            ------------------------------------------------
            -- IDLE SETTLE
            --
            -- Po zatrzymaniu avatar często nie dochodzi do
            -- celu (obrót niżej anulował jego AIMoveTo).
            -- Dopóki stoi za daleko: idziemy na dokładną
            -- pozycję i NIE obracamy, żeby nie anulować ruchu.
            -- Co IDLE_SETTLE_INTERVAL sprawdzamy postęp
            -- (Steer.settleStep): AIMoveTo ponawiamy tylko,
            -- gdy avatar się nie zbliża; cel nieosiągalny =
            -- jeden teleport albo koniec prób i zwykły obrót.
            -- Błąd w poziomie: sama różnica wysokości
            -- (schody, krawężnik) nie zatrzymuje obrotu.
            ------------------------------------------------

            local settleError =
                distance2(
                    current.x,
                    current.y,
                    S.targetX,
                    S.targetY
                )

            Steer.trackSettle(settleError)

            if not S.settleDone
                and (
                    settleError > IDLE_SETTLE_DISTANCE
                    or verticalError >= VERTICAL_SNAP
                )
            then

                S.settleAccumulator =
                    S.settleAccumulator +
                    delta

                if S.settleAccumulator >=
                    IDLE_SETTLE_INTERVAL
                then

                    S.settleAccumulator = 0.0

                    local settleStep =
                        Steer.settleStep(
                            settleError,
                            verticalError
                        )

                    if settleStep == "move" then

                        -- daleko w tyle: truchtem, blisko: spokojnie
                        local settleMoveType = "Walk"

                        if errorDistance > 1.5 then
                            settleMoveType = "Run"
                        end

                        if moveRemoteAI(
                            S.targetX,
                            S.targetY,
                            S.targetZ,
                            settleMoveType,
                            Sync.isRemoteCrouching()
                        ) then

                            S.lastCommandX = S.targetX
                            S.lastCommandY = S.targetY
                            S.lastCommandZ = S.targetZ
                        end

                    elseif settleStep == "teleport" then

                        Diag.log(
                            string.format(
                                "[CP2077Coop] EVENT avatar cannot walk to the remote spot: teleport, error=%.2f",
                                errorDistance
                            )
                        )

                        hardCorrectRemote(
                            player,
                            S.targetX,
                            S.targetY,
                            S.targetZ,
                            true
                        )

                        S.lastCommandX = S.targetX
                        S.lastCommandY = S.targetY
                        S.lastCommandZ = S.targetZ

                    elseif settleStep == "done" then

                        Diag.log(
                            string.format(
                                "[CP2077Coop] EVENT avatar cannot reach the remote spot: stays %.2f m away",
                                errorDistance
                            )
                        )

                        cancelMoveCommand()
                    end
                end

                if not S.settleDone then
                    return
                end

            elseif not S.settleDone then

                -- w tolerancji: kolejna seria liczy postęp od zera
                S.settleBest = nil
                S.settleStalls = 0
            end

            -- pierwsza korekta od razu po kolejnym zatrzymaniu
            S.settleAccumulator =
                IDLE_SETTLE_INTERVAL


            S.rotateAccumulator =
                S.rotateAccumulator +
                delta


            if S.rotateAccumulator >=
                ROTATE_INTERVAL
            then

                S.rotateAccumulator = 0.0

                -- avatar jeszcze dochodzi (AIMoveTo w toku, błąd maleje):
                -- obrót anulowałby ostatnie centymetry dojścia (avatar
                -- stawał do 0.35 m od gracza), więc czeka, aż stanie
                local approaching =
                    S.activeMoveCommand ~= nil
                    and settleError <
                        S.rotateLastError -
                        0.01

                S.rotateLastError = settleError

                local facingChanged = 999.0


                -- avatar przesunął się od ostatniego obrotu (spóźnione
                -- AIMoveTo, popchnięcie, reakcja na trafienie): obrócił
                -- się razem z ruchem, więc wyrównujemy go jeszcze raz
                if S.lastFacingX ~= nil
                    and distance2(
                        current.x,
                        current.y,
                        S.facingAtX,
                        S.facingAtY
                    ) <= Steer.FACING_MOVED
                then

                    facingChanged =
                        distance2(
                            S.remoteForwardX,
                            S.remoteForwardY,
                            S.lastFacingX,
                            S.lastFacingY
                        )
                end


                if facingChanged > 0.03
                    and not approaching
                then

                    cancelMoveCommand()

                    rotateRemote(
                        S.remoteForwardX,
                        S.remoteForwardY
                    )

                    S.lastFacingX =
                        S.remoteForwardX

                    S.lastFacingY =
                        S.remoteForwardY

                    S.facingAtX = current.x
                    S.facingAtY = current.y
                end
            end
        end
    end
)
