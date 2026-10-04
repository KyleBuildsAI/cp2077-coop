------------------------------------------------------------
-- CP2077 COOP
--
-- v0.0.31 WORLD + STATE + VEHICLE + COMBAT SYNC + DIAGNOSTICS + TEST BOT
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
local JOIN_OFFSET = 1.75

-- Przez krótki czas ponawiamy teleport prawdziwego P2,
-- gdyby pierwszy został odrzucony podczas streamingu świata.
local JOIN_SYNC_INTERVAL = 0.25
local JOIN_SYNC_DURATION = 2.00
local JOIN_SYNC_TOLERANCE = 2.50

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

S.joinSyncPending = false
S.joinAttempts = 0
S.joinSyncElapsed = 0.0
S.joinSyncAccumulator = 0.0


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

    names = {},
    hashes = {},
    localSet = {},

    sendIndex = 1,
    sendLow = false,

    pendingHigh = nil,
    building = {},
    lastCycle = nil,
    previousCycle = nil,
    cyclesReceived = 0,
    compared = false
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

        print("[CP2077Coop] no modlist.txt - run coop-tools/devkit.py modlist or coop_monitor.py")
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

    print(string.format("[CP2077Coop] mod list loaded: %d mods", #Mods.names))
end


-- Następny pakiet z odciskiem (wysyłane w pętli bez końca).
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
    else
        Mods.sendIndex = Mods.sendIndex + 1
    end

    return
        Mods.TYPE_LO * Mods.STRIDE +
        hash % 256 +
        finish
end


function Mods.receive(packetType, value)

    local flag =
        value >= 256

    local byte =
        value % 256

    if packetType == Mods.TYPE_HI then

        if flag then
            Mods.building = {}
        end

        Mods.pendingHigh = byte
        return
    end

    -- TYPE_LO bez poprzedzającego HI (zgubiony pakiet): pomijamy
    if Mods.pendingHigh == nil then
        return
    end

    local hash =
        Mods.pendingHigh * 256 +
        byte

    Mods.pendingHigh = nil

    if hash ~= Mods.EMPTY_SENTINEL then
        Mods.building[hash] = true
    end

    if flag then

        Mods.previousCycle = Mods.lastCycle
        Mods.lastCycle = Mods.building
        Mods.building = {}
        Mods.cyclesReceived = Mods.cyclesReceived + 1

        if not Mods.compared and Mods.cyclesReceived >= 2 then

            Mods.compared = true
            Mods.logComparison()
        end
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

    print(
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
        print("[CP2077Coop] MOD only you: " .. name)
    end
end


function Mods.resetRemote()

    Mods.pendingHigh = nil
    Mods.building = {}
    Mods.lastCycle = nil
    Mods.previousCycle = nil
    Mods.cyclesReceived = 0
    Mods.compared = false
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

    MAX_JOIN_ATTEMPTS = 3,

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

    sendSlot = 0,

    remoteFlags = 0,
    appliedFlags = -1,

    remoteTimeMinutes = -1,
    timeCooldown = 0.0,

    appliedWeather = -1,

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

    print(
        "[CP2077Coop] redscript not compiled - remote avatar and state sync disabled (check r6/logs/redscript_rCURRENT.log; an error in any mod's .reds breaks the whole compile)"
    )
end


-- Co wysłać w tym pakiecie. Host co drugi pakiet
-- wysyła stan świata, flagi gracza lecą zawsze co drugi.
function Sync.buildPayload(player, isHost)

    -- odpowiedź na ping drugiego gracza ma pierwszeństwo
    if Sync.pendingPong ~= nil then

        local token = Sync.pendingPong
        Sync.pendingPong = nil

        return
            Sync.TYPE_PONG * Sync.TYPE_STRIDE +
            token
    end

    if Sync.clock - Sync.lastPingClock >=
        Sync.PING_INTERVAL
    then

        Sync.pingToken =
            (Sync.pingToken + 1) %
            Sync.TYPE_STRIDE

        Sync.pingSentAt = Sync.clock
        Sync.lastPingClock = Sync.clock

        return
            Sync.TYPE_PING * Sync.TYPE_STRIDE +
            Sync.pingToken
    end

    local roleFlag = 0

    if isHost then
        roleFlag = Sync.FLAG_HOST
    end

    if not Sync.hasScripts(player) then

        Sync.reportMissingScripts()

        Sync.realLocalFlags = roleFlag

        Sync.localFlags =
            (Sync.flagsOverride or 0) +
            roleFlag

        return
            Sync.TYPE_FLAGS * Sync.TYPE_STRIDE +
            Sync.localFlags
    end

    -- flagi czytamy przy każdym pakiecie (panel + blokada teleportu w aucie)
    -- (pojazd: patrz niżej)
    -- flagsOverride: ustawiane przez bota testowego (Bot); blokada
    -- teleportu i poza auta patrzą na prawdziwe flagi gracza
    local realFlags =
        player:CP2077Coop_GetStateFlags()

    Sync.realLocalFlags =
        realFlags +
        roleFlag

    Sync.localFlags =
        (Sync.flagsOverride or realFlags) +
        roleFlag

    Sync.sendSlot = Sync.sendSlot + 1

    -- w pojeździe: co 6. pakiet (nieparzysty, nie koliduje z hostem) = model pojazdu
    if Sync.hasFlag(Sync.localFlags, Sync.FLAG_IN_VEHICLE)
        and Sync.sendSlot % 6 == 3
    then

        local index = Sync.vehicleIndexOverride

        if index == nil
            and player.CP2077Coop_GetMountedVehicleIndex ~= nil
        then
            index = player:CP2077Coop_GetMountedVehicleIndex()
        end

        if index ~= nil and index >= 0 then

            return
                Sync.TYPE_VEHICLE * Sync.TYPE_STRIDE +
                index
        end
    end

    -- lista modów: co 4. pakiet (nieparzysty)
    if Sync.sendSlot % 4 == 1 then
        return Mods.nextPayload()
    end

    if isHost
        and Sync.sendSlot % 2 == 0
    then

        if Sync.sendSlot % 4 == 0 then

            local minutes =
                player:CP2077Coop_GetTimeOfDayMinutes()

            return
                Sync.TYPE_TIME * Sync.TYPE_STRIDE +
                math.floor(minutes / Sync.TIME_STEP_MINUTES)
        end

        local weather =
            player:CP2077Coop_GetWeatherIndex()

        return
            Sync.TYPE_WEATHER * Sync.TYPE_STRIDE +
            (weather + 1)
    end

    return
        Sync.TYPE_FLAGS * Sync.TYPE_STRIDE +
        Sync.localFlags
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

    elseif packetType == Sync.TYPE_TIME then

        -- poza dobą = uszkodzony pakiet, nie godzina
        if value * Sync.TIME_STEP_MINUTES >= 1440 then
            return
        end

        Sync.remoteTimeMinutes =
            value *
            Sync.TIME_STEP_MINUTES

    elseif packetType == Sync.TYPE_WEATHER then

        Sync.remoteWeather =
            value - 1

    elseif packetType == Sync.TYPE_PING then

        Sync.pendingPong = value

    elseif packetType == Sync.TYPE_PONG then

        Sync.onPong(value)

    elseif packetType == Sync.TYPE_VEHICLE then

        Sync.remoteVehicleIndex = value

    elseif packetType == Mods.TYPE_HI
        or packetType == Mods.TYPE_LO
    then

        Mods.receive(packetType, value)
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


-- Joiner przejmuje godzinę i pogodę hosta.
function Sync.applyWorldState(player, isHost, delta)

    if isHost
        or not Sync.hasScripts(player)
    then
        return
    end

    Sync.timeCooldown =
        math.max(
            0.0,
            Sync.timeCooldown - delta
        )

    if Sync.remoteTimeMinutes >= 0
        and Sync.timeCooldown <= 0.0
    then

        local localMinutes =
            player:CP2077Coop_GetTimeOfDayMinutes()

        -- różnica na zegarze 24h (23:59 vs 00:01 = 2 min)
        local difference =
            math.abs(
                localMinutes -
                Sync.remoteTimeMinutes
            )

        difference =
            math.min(
                difference,
                1440 - difference
            )

        if difference >
            Sync.TIME_TOLERANCE_MINUTES
        then

            player:CP2077Coop_SetTimeOfDayMinutes(
                Sync.remoteTimeMinutes
            )

            print(
                string.format(
                    "[CP2077Coop] time synced to host %02d:%02d",
                    math.floor(Sync.remoteTimeMinutes / 60),
                    Sync.remoteTimeMinutes % 60
                )
            )
        end

        Sync.timeCooldown =
            Sync.TIME_APPLY_COOLDOWN
    end


    if Sync.remoteWeather ~= nil
        and Sync.remoteWeather >= 0
        and Sync.remoteWeather ~= Sync.appliedWeather
    then

        player:CP2077Coop_SetWeatherIndex(
            Sync.remoteWeather
        )

        Sync.appliedWeather =
            Sync.remoteWeather

        print(
            "[CP2077Coop] weather synced to host, index "
            .. tostring(Sync.remoteWeather)
        )
    end
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
------------------------------------------------------------

-- Pierwszy pakiet drugiego gracza: avatar przed lokalnym graczem.
function Sync.requestSpawn(player)

    -- redscript się nie skompilował: bez avatara (log raz),
    -- reszta onUpdate (teleport do hosta, panel) działa dalej
    if player.CP2077Coop_SpawnRemoteTest == nil then

        Sync.reportMissingScripts()
        return
    end

    player:CP2077Coop_SpawnRemoteTest()

    S.remoteInitialized = true

    print(
        string.format(
            "[CP2077Coop] remote spawn requested @ %.2f %.2f %.2f",
            S.targetX,
            S.targetY,
            S.targetZ
        )
    )
end


function Sync.reset()

    Sync.pendingPong = nil
    Sync.pingSentAt = nil
    Sync.rttMs = nil
    Sync.rttLastMs = nil
    Sync.rttMinMs = nil
    Sync.rttMaxMs = nil
    Sync.rttSamples = 0

    Sync.sendSlot = 0
    Sync.remoteFlags = 0
    Sync.appliedFlags = -1
    Sync.remoteTimeMinutes = -1
    Sync.remoteWeather = nil
    Sync.timeCooldown = 0.0
    Sync.appliedWeather = -1
    Sync.remoteFlagsSeen = false
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

            print(
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
    hitsUnmatched = 0
}


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

        print("[CP2077Coop] COMBAT scan error: " .. tostring(err))
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

        print("[CP2077Coop] COMBAT damage error: " .. tostring(err))
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

    print("[CP2077Coop] EVENT test pattern started")
end


function Bot.stop()

    if not Bot.active then
        return
    end

    Bot.active = false
    Sync.flagsOverride = nil
    Sync.vehicleIndexOverride = nil

    print("[CP2077Coop] EVENT test pattern stopped")
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

        print(
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

        print(
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
-- Log: linia [STATS] co STATS_INTERVAL s w CP2077Coop.log,
-- czytana przez tools/coop_monitor.py.
------------------------------------------------------------

local Diag = {
    VERSION = "0.0.31",

    STATS_INTERVAL = 5.0,
    MONITOR_READ_INTERVAL = 2.0,

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

    visible = true,

    packetsReceived = 0,
    packetsSent = 0,
    missed = 0,
    ignored = 0,

    windowReceived = 0,
    windowSent = 0,
    windowTimer = 0.0,
    ppsIn = 0.0,
    ppsOut = 0.0,

    lastPacketClock = nil,
    lastState = "WAITING",

    statsTimer = 0.0,
    monitorTimer = 0.0,
    monitor = {},

    avatarError = nil,

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


function Diag.recordDrift(distance)

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

        print("[CP2077Coop] could not save role.txt")
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

    print(
        "[CP2077Coop] EVENT role changed to "
        .. (isHost and "HOST" or "JOINER")
    )
end


-- Wywoływane dla każdego nowszego pakietu.
function Diag.onPacket(sequence, previousSequence)

    Diag.packetsReceived =
        Diag.packetsReceived + 1

    Diag.windowReceived =
        Diag.windowReceived + 1

    -- luka w sekwencji = zgubione w sieci LUB nadpisane w DLL
    -- (DLL trzyma tylko ostatni pakiet, a Lua czyta raz na klatkę)
    if previousSequence >= 0
        and sequence > previousSequence + 1
    then

        Diag.missed =
            Diag.missed +
            (sequence - previousSequence - 1)
    end

    Diag.lastPacketClock = Sync.clock
end


function Diag.onIgnored()

    Diag.ignored =
        Diag.ignored + 1
end


function Diag.onSent()

    Diag.packetsSent =
        Diag.packetsSent + 1

    Diag.windowSent =
        Diag.windowSent + 1
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


function Diag.missedPercent()

    local expected =
        Diag.packetsReceived +
        Diag.missed

    if expected == 0 then
        return 0.0
    end

    return
        100.0 *
        Diag.missed /
        expected
end


function Diag.roleConflict()

    if not Sync.remoteFlagsSeen then
        return false
    end

    local remoteIsHost =
        Sync.hasFlag(
            Sync.remoteFlags,
            Sync.FLAG_HOST
        )

    return remoteIsHost == IS_HOST
end


-- Druga strona bez ping/pong = starsza wersja moda.
function Diag.peerLooksOutdated()

    return
        Diag.packetsReceived > 150
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
        Diag.packetAge()

    return
        string.format(
            "[CP2077Coop] [STATS] state=%s role=%s rtt_ms=%s rtt_min=%s rtt_max=%s rtt_n=%d pps_in=%.1f pps_out=%.1f missed_pct=%.1f ignored=%d age_ms=%s avatar_err_m=%s drift_avg_m=%s drift_max_m=%s remote_speed=%.1f move=%s remote_flags=%d bot=%s hits_in=%d hits_applied=%d hits_unmatched=%d mods_you=%d mods_partner=%s mods_shared=%s conflict=%s peer_old=%s torn=%d",
            Diag.connectionState(),
            IS_HOST and "host" or "joiner",
            Diag.formatMs(Sync.rttMs),
            Diag.formatMs(Sync.rttMinMs),
            Diag.formatMs(Sync.rttMaxMs),
            Sync.rttSamples,
            Diag.ppsIn,
            Diag.ppsOut,
            Diag.missedPercent(),
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
            Diag.tornReads
        )
end


-- Monitor (tools/coop_monitor.py) zapisuje tu IP serwera i ping.
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

    Diag.monitor = values
end


function Diag.tick(delta)

    Diag.windowTimer =
        Diag.windowTimer + delta

    if Diag.windowTimer >= 1.0 then

        Diag.ppsIn =
            Diag.windowReceived /
            Diag.windowTimer

        Diag.ppsOut =
            Diag.windowSent /
            Diag.windowTimer

        Diag.windowReceived = 0
        Diag.windowSent = 0
        Diag.windowTimer = 0.0
    end


    local state =
        Diag.connectionState()

    if state ~= Diag.lastState then

        print(
            "[CP2077Coop] EVENT connection "
            .. Diag.lastState
            .. " -> "
            .. state
        )

        Diag.lastState = state
    end


    Diag.statsTimer =
        Diag.statsTimer + delta

    if Diag.statsTimer >= Diag.STATS_INTERVAL then

        Diag.statsTimer = 0.0
        Diag.closeDriftWindow()
        print(Diag.statsLine())
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


function Diag.levelForRtt(rtt)

    if rtt == nil then
        return "neutral"
    end

    if rtt < 350 then
        return "good"
    end

    if rtt < 600 then
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


function Diag.draw()

    if not Diag.visible then
        return
    end

    ImGui.SetNextWindowPos(20, 300, ImGuiCond.FirstUseEver)

    if not ImGui.Begin("CP2077 Coop", ImGuiWindowFlags.AlwaysAutoResize) then

        ImGui.End()
        return
    end


    -- POŁĄCZENIE
    local state =
        Diag.connectionState()

    local stateLevel = "bad"

    if state == "OK" then
        stateLevel = "good"
    elseif state == "STALE" or state == "WAITING" then
        stateLevel = "warn"
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
    end


    ImGui.Separator()

    -- OPÓŹNIENIE
    Diag.row("Player RTT", Diag.formatMs(Sync.rttMs) .. " ms", Diag.levelForRtt(Sync.rttMs))
    Diag.row(
        "RTT min/max",
        Diag.formatMs(Sync.rttMinMs) .. " / " .. Diag.formatMs(Sync.rttMaxMs) .. " ms",
        "neutral"
    )

    local age =
        Diag.packetAge()

    local ageLevel = "good"

    if age == nil or age >= Diag.STALE_AFTER then
        ageLevel = "bad"
    elseif age >= 0.25 then
        ageLevel = "warn"
    end

    Diag.row(
        "Last packet",
        age and string.format("%.0f ms ago", age * 1000.0) or "never",
        ageLevel
    )

    Diag.row(
        "Packets in/out",
        string.format("%.1f / %.1f per s", Diag.ppsIn, Diag.ppsOut),
        Diag.ppsIn >= 20 and "good" or "warn"
    )

    local missed =
        Diag.missedPercent()

    Diag.row(
        "Missed",
        string.format("%.1f %%  (late/dup %d)", missed, Diag.ignored),
        missed < 10 and "good" or (missed < 25 and "warn" or "bad")
    )


    ImGui.Separator()

    -- SERWER (z coop_monitor.py)
    local server =
        Diag.monitor.server or "start tools/coop_monitor.py"

    Diag.row("Relay server", server, Diag.monitor.server and "neutral" or "warn")

    if Diag.monitor.server_ping_ms ~= nil then

        local serverPing =
            tonumber(Diag.monitor.server_ping_ms)

        Diag.row(
            "Your ping to relay",
            Diag.monitor.server_ping_ms .. " ms",
            Diag.levelForRtt(serverPing and serverPing * 1.5 or nil)
        )
    end

    if Diag.monitor.server_location ~= nil then
        Diag.row("Relay location", Diag.monitor.server_location, "neutral")
    end


    ImGui.Separator()

    -- AVATAR / STAN
    Diag.row(
        "Avatar",
        S.remoteHandle ~= nil and "spawned" or (S.remoteInitialized and "spawning" or "none"),
        S.remoteHandle ~= nil and "good" or "warn"
    )

    if Diag.avatarError ~= nil then

        Diag.row(
            "Avatar drift",
            string.format("%.2f m", Diag.avatarError),
            Diag.avatarError < 1.5 and "good" or (Diag.avatarError < 4 and "warn" or "bad")
        )
    end

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
        print(Diag.statsLine())
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

    if player.CP2077Coop_ShowRemoteVehicle == nil then
        return
    end

    local lost =
        Diag.connectionState() == "LOST"

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
                print("[CP2077Coop] EVENT remote vehicle hidden: connection lost")
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

            print("[CP2077Coop] EVENT remote is driving: avatar parked")
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

            Diag.onSent()
        end
    end

    S.sendPrevX = pos.x
    S.sendPrevY = pos.y
    S.sendPrevZ = pos.z
    S.sendPrevSource = source
end


------------------------------------------------------------
-- TELEPORT REMOTE AVATAR
------------------------------------------------------------

local function hardCorrectRemote(
    player,
    x,
    y,
    z
)

    -- bez redscriptu nie ma czym teleportować (i nie ma avatara)
    if player.CP2077Coop_MoveRemoteTest == nil then

        Sync.reportMissingScripts()
        return
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

        print(
            "[CP2077Coop] PLAYER TELEPORT ERROR: "
            .. tostring(err)
        )

        return false
    end

    return true
end


------------------------------------------------------------
-- BEGIN P2 -> HOST WORLD SYNC
------------------------------------------------------------

local function beginJoinWorldSync(
    hostX,
    hostY,
    hostZ,
    hostForwardX,
    hostForwardY
)

    if S.worldJoinComplete
        or S.joinSyncPending
    then
        return
    end

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


    S.joinSyncPending = true
    S.joinSyncElapsed = 0.0

    -- Pierwsza próba od razu.
    S.joinSyncAccumulator =
        JOIN_SYNC_INTERVAL


    print(
        string.format(
            "[CP2077Coop] P2 WORLD SYNC -> %.2f %.2f %.2f",
            S.joinTargetX,
            S.joinTargetY,
            S.joinTargetZ
        )
    )
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

    S.joinSyncPending = false
    S.joinAttempts = 0
    S.joinSyncElapsed = 0.0
    S.joinSyncAccumulator = 0.0

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

        print(
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

        Sync.tick(delta)
        Diag.tick(delta)


        -- zmiana roli z panelu: start sesji od nowa
        if Diag.roleChanged then

            Diag.roleChanged = false
            resetRemote()
        end


        -- przycisk "Teleport to host" w panelu
        if Diag.teleportRequested then

            Diag.teleportRequested = false

            if not IS_HOST then

                S.worldJoinComplete = false
                S.joinAttempts = 0

                print("[CP2077Coop] EVENT manual teleport to host requested")
            end
        end


        ----------------------------------------------------
        -- GAMEPLAY STATE
        ----------------------------------------------------

        local loaded =
            isGameplayLoaded()


        if loaded ~= S.syncActive then

            S.syncActive = loaded


            if S.syncActive then

                print(
                    "[CP2077Coop] sync ON"
                )

                resetRemote()

            else

                print(
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


        ----------------------------------------------------
        -- ROLE
        ----------------------------------------------------

        if not S.rolePrinted then

            S.rolePrinted = true

            if IS_HOST then

                print(
                    "[CP2077Coop] ROLE = HOST"
                )

            else

                print(
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

                print("[CP2077Coop] EVENT teleported to test area: " .. area.name)

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
        -- NO FRESH REMOTE DATA
        ----------------------------------------------------

        if not Game.CP2077Coop_HasRemotePlayer() then
            return
        end


        ----------------------------------------------------
        -- NEW REMOTE NETWORK STATE
        ----------------------------------------------------

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

                print(
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
            ------------------------------------------------

            -- nie teleportujemy gracza siedzącego w pojeździe
            -- (prawdziwe flagi: faza "vehicle" bota go nie blokuje)
            local localInVehicle =
                Sync.hasFlag(
                    Sync.realLocalFlags,
                    Sync.FLAG_IN_VEHICLE
                )

            if not IS_HOST
                and not S.worldJoinComplete
                and not S.joinSyncPending
                and not localInVehicle
            then

                beginJoinWorldSync(
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

            -- joiner w trakcie teleportu do hosta: spawn dopiero po nim
            -- (avatar pojawia się przed lokalnym graczem, nie w miejscu,
            -- które gracz właśnie opuszcza)
            if not S.remoteInitialized
                and not S.joinSyncPending
            then

                Sync.requestSpawn(
                    player
                )
            end
        end


        ----------------------------------------------------
        -- P2 REAL PLAYER WORLD-SYNC TELEPORT
        ----------------------------------------------------

        if S.joinSyncPending then

            S.joinSyncElapsed =
                S.joinSyncElapsed +
                delta

            S.joinSyncAccumulator =
                S.joinSyncAccumulator +
                delta


            if S.joinSyncAccumulator >=
                JOIN_SYNC_INTERVAL
            then

                S.joinSyncAccumulator = 0.0

                teleportLocalPlayer(
                    player,
                    S.joinTargetX,
                    S.joinTargetY,
                    S.joinTargetZ,
                    S.joinForwardX,
                    S.joinForwardY
                )
            end


            local playerPos =
                player:
                    GetWorldPosition()


            local joinError =
                distance3(
                    playerPos.x,
                    playerPos.y,
                    playerPos.z,

                    S.joinTargetX,
                    S.joinTargetY,
                    S.joinTargetZ
                )


            if joinError <=
                JOIN_SYNC_TOLERANCE
            then

                S.joinSyncPending = false
                S.worldJoinComplete = true

                print(
                    string.format(
                        "[CP2077Coop] WORLD SYNC OK error=%.2f",
                        joinError
                    )
                )

            elseif S.joinSyncElapsed >=
                JOIN_SYNC_DURATION
            then

                S.joinSyncPending = false

                S.joinAttempts =
                    S.joinAttempts + 1

                print(
                    string.format(
                        "[CP2077Coop] WORLD SYNC FAILED error=%.2f attempt=%d/%d",
                        joinError,
                        S.joinAttempts,
                        Sync.MAX_JOIN_ATTEMPTS
                    )
                )

                -- Wcześniej: ponawianie w nieskończoność, co 2 s
                -- szarpało gracza i zamrażało avatar. Teraz limit;
                -- ręcznie: przycisk "Teleport to host" w panelu.
                if S.joinAttempts >=
                    Sync.MAX_JOIN_ATTEMPTS
                then

                    S.worldJoinComplete = true

                    print(
                        "[CP2077Coop] WORLD SYNC GAVE UP - use 'Teleport to host' in the coop panel"
                    )
                end
            end

            -- W tym ticku nie ruszamy jeszcze remote AI.
            return
        end


        ----------------------------------------------------
        -- WAIT FOR REMOTE NPC HANDLE
        ----------------------------------------------------

        if S.remoteInitialized
            and S.remoteHandle == nil
        then

            S.remoteHandle =
                getRemoteHandle()


            if S.remoteHandle ~= nil then

                print(
                    "[CP2077Coop] remote entity acquired"
                )

                local playerPos =
                    player:
                        GetWorldPosition()

                local remotePos =
                    S.remoteHandle:
                        GetWorldPosition()


                print(
                    string.format(
                        "[CP2077Coop] LOCAL %.2f %.2f %.2f",
                        playerPos.x,
                        playerPos.y,
                        playerPos.z
                    )
                )

                print(
                    string.format(
                        "[CP2077Coop] REMOTE TARGET %.2f %.2f %.2f",
                        S.targetX,
                        S.targetY,
                        S.targetZ
                    )
                )

                print(
                    string.format(
                        "[CP2077Coop] JUDY BEFORE SNAP %.2f %.2f %.2f",
                        remotePos.x,
                        remotePos.y,
                        remotePos.z
                    )
                )


                S.spawnSnapPending = true
                S.spawnSnapElapsed = 0.0
                S.spawnSnapAccumulator =
                    SPAWN_SNAP_INTERVAL


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

        -- avatar czeka ukryty; czas i pogoda hosta działają dalej
        if Sync.parkAvatar(player) then

            Sync.applyWorldState(
                player,
                IS_HOST,
                delta
            )

            return
        end


        ----------------------------------------------------
        -- FORCE INITIAL REMOTE POSITION
        ----------------------------------------------------

        if S.spawnSnapPending then

            S.spawnSnapElapsed =
                S.spawnSnapElapsed +
                delta

            S.spawnSnapAccumulator =
                S.spawnSnapAccumulator +
                delta


            if S.spawnSnapAccumulator >=
                SPAWN_SNAP_INTERVAL
            then

                S.spawnSnapAccumulator = 0.0

                hardCorrectRemote(
                    player,
                    S.targetX,
                    S.targetY,
                    S.targetZ
                )
            end


            local snapPos =
                S.remoteHandle:
                    GetWorldPosition()


            local snapError =
                distance3(
                    snapPos.x,
                    snapPos.y,
                    snapPos.z,

                    S.targetX,
                    S.targetY,
                    S.targetZ
                )


            if snapError <=
                SPAWN_SNAP_TOLERANCE
            then

                S.spawnSnapPending = false

                print(
                    string.format(
                        "[CP2077Coop] REMOTE SNAP OK error=%.2f",
                        snapError
                    )
                )

            elseif S.spawnSnapElapsed >=
                SPAWN_SNAP_DURATION
            then

                S.spawnSnapPending = false

                print(
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

        -- stan gracza (kucanie, broń) i świata (czas, pogoda)
        Sync.applyRemoteFlags(player)

        Sync.applyWorldState(
            player,
            IS_HOST,
            delta
        )

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

        Diag.recordDrift(errorDistance)


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
                S.targetZ
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

                        print(
                            string.format(
                                "[CP2077Coop] EVENT avatar cannot walk to the remote spot: teleport, error=%.2f",
                                errorDistance
                            )
                        )

                        hardCorrectRemote(
                            player,
                            S.targetX,
                            S.targetY,
                            S.targetZ
                        )

                        S.lastCommandX = S.targetX
                        S.lastCommandY = S.targetY
                        S.lastCommandZ = S.targetZ

                    elseif settleStep == "done" then

                        print(
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
