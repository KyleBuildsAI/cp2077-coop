------------------------------------------------------------
-- CP2077 COOP
--
-- v0.0.26 WORLD SYNC
--
-- MANUAL ROLE FOR CURRENT TEST BUILD
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
-- wartość - prędkość liczona jest z numeru sekwencji.
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

-- po przycięciu gry nie wysyłamy zaległych pakietów seriami
local MAX_SEND_BACKLOG = 2

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
S.joinSyncElapsed = 0.0
S.joinSyncAccumulator = 0.0


------------------------------------------------------------
-- SESSION / NETWORK
------------------------------------------------------------

S.syncActive = false

S.rolePrinted = false
S.lastRemoteSequence = -1


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
    moveType
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

    command.alwaysUseStealth =
        false


    cancelMoveCommand()

    controller:
        SendCommand(command)

    S.activeMoveCommand = command

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
-- TELEPORT REMOTE AVATAR
------------------------------------------------------------

local function hardCorrectRemote(
    player,
    x,
    y,
    z
)

    cancelMoveCommand()

    player:
        CP2077Coop_MoveRemoteTest(
            x,
            y,
            z
        )
end


------------------------------------------------------------
-- TELEPORT REAL LOCAL PLAYER
------------------------------------------------------------

local function teleportLocalPlayer(
    player,
    x,
    y,
    z
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

            local orientation =
                player:
                    GetWorldOrientation()

            Game.GetTeleportationFacility():
                Teleport(
                    player,
                    position,
                    orientation
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

    S.remoteInitialized = false
    S.remoteHandle = nil

    S.lastRemoteSequence = -1

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
    S.joinSyncElapsed = 0.0
    S.joinSyncAccumulator = 0.0

    S.worldJoinComplete = IS_HOST

    S.joinTargetX = 0.0
    S.joinTargetY = 0.0
    S.joinTargetZ = 0.0

    S.sendAccumulator = 0.0
    S.commandAccumulator = 0.0
    S.rotateAccumulator = 0.0

    S.rolePrinted = false
end


------------------------------------------------------------
-- INIT
------------------------------------------------------------

registerForEvent(
    "onInit",
    function()

        print(
            "[CP2077Coop] bridge v0.0.26 WORLD SYNC loaded"
        )

    end
)


------------------------------------------------------------
-- UPDATE
------------------------------------------------------------

registerForEvent(
    "onUpdate",
    function(delta)


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
        -- LOCAL PLAYER -> VPS
        ----------------------------------------------------

        S.sendAccumulator =
            S.sendAccumulator +
            delta


        if S.sendAccumulator >=
            SEND_INTERVAL
        then

            S.sendAccumulator =
                S.sendAccumulator -
                SEND_INTERVAL

            -- po przycięciu nie nadrabiamy serią identycznych pozycji
            -- (druga strona widziałaby wtedy IDLE)
            S.sendAccumulator =
                math.min(
                    S.sendAccumulator,
                    SEND_INTERVAL * MAX_SEND_BACKLOG
                )


            local pos =
                player:
                    GetWorldPosition()


            local forward =
                player:
                    GetWorldForward()


            Game.CP2077Coop_PushPlayerState(
                pos.x,
                pos.y,
                pos.z,
                pos.w,
                forward.x,
                forward.y
            )
        end


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
        -- cofałby postać. Duży spadek = restart drugiego klienta.
        ----------------------------------------------------

        local isNewer =
            sequence >
            S.lastRemoteSequence

        local isRestart =
            sequence <
            S.lastRemoteSequence -
            SEQUENCE_RESET_GAP

        if isNewer
            or isRestart
        then

            local previousSequence =
                S.lastRemoteSequence

            S.lastRemoteSequence =
                sequence


            local rx =
                Game.CP2077Coop_GetRemoteX()

            local ry =
                Game.CP2077Coop_GetRemoteY()

            local rz =
                Game.CP2077Coop_GetRemoteZ()


            S.remoteForwardX =
                Game.CP2077Coop_GetRemoteForwardX()

            S.remoteForwardY =
                Game.CP2077Coop_GetRemoteForwardY()


            ------------------------------------------------
            -- P2 JOINS P1 WORLD
            ------------------------------------------------

            if not IS_HOST
                and not S.worldJoinComplete
                and not S.joinSyncPending
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

                local sequenceDelta =
                    sequence -
                    previousSequence

                if sequenceDelta <= 0 then
                    sequenceDelta = 1
                end

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
                S.remoteVerticalJump =
                    math.abs(dz) >
                    VERTICAL_SNAP *
                    0.5


                if packetDistance >
                    MOVEMENT_EPSILON
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

            if not S.remoteInitialized then

                player:
                    CP2077Coop_SpawnRemoteTest()

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
                    S.joinTargetZ
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

                print(
                    string.format(
                        "[CP2077Coop] WORLD SYNC FAILED error=%.2f",
                        joinError
                    )
                )
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

        if (snapFollow and targetMoved)
            or errorDistance >= TELEPORT_DISTANCE
        then

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


        ----------------------------------------------------
        -- REMOTE MOVING
        ----------------------------------------------------

        if S.remoteMoving then

            S.commandAccumulator =
                S.commandAccumulator +
                delta


            if S.commandAccumulator >=
                COMMAND_INTERVAL
            then

                S.commandAccumulator = 0.0


                local targetChanged =
                    distance3(
                        S.targetX,
                        S.targetY,
                        S.targetZ,

                        S.lastCommandX,
                        S.lastCommandY,
                        S.lastCommandZ
                    )


                if targetChanged >=
                    MIN_TARGET_CHANGE
                then

                    if moveRemoteAI(
                        S.targetX,
                        S.targetY,
                        S.targetZ,
                        S.movementType
                    ) then

                        S.lastCommandX = S.targetX
                        S.lastCommandY = S.targetY
                        S.lastCommandZ = S.targetZ
                    end
                end
            end


        ----------------------------------------------------
        -- REMOTE IDLE
        ----------------------------------------------------

        else

            S.commandAccumulator = 0.0


            ------------------------------------------------
            -- IDLE SETTLE
            --
            -- Po zatrzymaniu avatar często nie dochodzi do
            -- celu (obrót niżej anulował jego AIMoveTo).
            -- Dopóki stoi za daleko: idziemy na dokładną
            -- pozycję (ponawiane co IDLE_SETTLE_INTERVAL)
            -- i NIE obracamy, żeby nie anulować ruchu.
            ------------------------------------------------

            if errorDistance >
                IDLE_SETTLE_DISTANCE
            then

                S.settleAccumulator =
                    S.settleAccumulator +
                    delta

                if S.settleAccumulator >=
                    IDLE_SETTLE_INTERVAL
                then

                    S.settleAccumulator = 0.0

                    -- daleko w tyle: truchtem, blisko: spokojnie
                    local settleMoveType = "Walk"

                    if errorDistance > 1.5 then
                        settleMoveType = "Run"
                    end

                    if moveRemoteAI(
                        S.targetX,
                        S.targetY,
                        S.targetZ,
                        settleMoveType
                    ) then

                        S.lastCommandX = S.targetX
                        S.lastCommandY = S.targetY
                        S.lastCommandZ = S.targetZ
                    end
                end

                return
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

                local facingChanged = 999.0


                if S.lastFacingX ~= nil then

                    facingChanged =
                        distance2(
                            S.remoteForwardX,
                            S.remoteForwardY,
                            S.lastFacingX,
                            S.lastFacingY
                        )
                end


                if facingChanged > 0.03 then

                    cancelMoveCommand()

                    rotateRemote(
                        S.remoteForwardX,
                        S.remoteForwardY
                    )

                    S.lastFacingX =
                        S.remoteForwardX

                    S.lastFacingY =
                        S.remoteForwardY
                end
            end
        end
    end
)
