# Inactive player-motor integration reference

These files preserve the previous mirror runtime at
`9fe8ff10cbb1e1b5f0fb9c250ea0215c966a38a9` byte for byte:

- `init_motor.lua`: the PR5 movement entrypoint, including its value-only
  `playerDiagnostics()` API and actor cleanup.
- `remote_motor.reds`: its engine locomotion, snap and turn helpers, together
  with the historical proxy spawn declaration.

They are not loaded, compiled into the active game scripts, or packaged by the
session package script. Do not install them alongside the active runtime:
`remote_motor.reds` declares the same proxy-spawn method as the current script.

The original `runtime/session/cet/CP2077Coop/player_motor.lua`, its standalone
regression tests and the other player-presentation experiments remain unchanged.
The active entrypoint now uses only upstream `player_pose.lua` from `97d7c1a`.
Its owned AI teleport command addresses observed proxy placement, not natural
locomotion or synchronized weapons/posture. The old motor still has a failed
smooth-tracking gate and remains reference work, not a second active controller.

Tools that used the archived entrypoint's `playerDiagnostics()` fields do not
receive that API from the new active entrypoint. Use the pose adapter's existing
logs and readback for the current experiment; do not fabricate old motor metrics.
Any later locomotion integration must deliberately choose one actuator per actor
and carry its lifecycle/measurement tests across.
