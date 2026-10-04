# Actual-avatar steering trace

For an instrumented comparison, create an empty `steering-trace.txt` in each
chosen instance's `CP2077Coop` mod folder before loading CET. This is independent
of `native_retarget`; run the same route and relay profile with that option off
and on. Tracing is off by default and its gate is checked once at `onInit`.

The mod writes `coop_steering_host.csv` or `coop_steering_joiner.csv` in its own
folder, at most 10 observations per second. A slow frame produces one observed
row, never invented intermediate positions. Writes are buffered for up to a
second and flushed on session reset or shutdown. The first write for each role
in a Lua lifetime replaces that role's prior CSV; archive it before restarting
CET. A trace stops at 6000 rows or 600 seconds of `Sync.clock` since its first
avatar observation. Session resets do not restart these limits. The gate and
outputs are ignored by Git and excluded from instance cloning/deployment.

Rows distinguish the actual visible remote avatar (`avatar_*`), the steering
target (`target_*`; native buffered sample in v2), and newest received position
(`raw_*`). `velocity_*` and `remote_speed` describe network motion, not measured
NPC locomotion. `t_s` is the mod's monotonic update clock; `wall_unix_s` provides
a coarse wall-clock alignment with the capture log. `local_bot_phase` belongs
to the local sender, not the remote avatar: the two bots may have different phases.

Use successive observed avatar positions to calculate displacement speed only
within a continuous session/avatar generation, and inspect gaps and teleport
requests. `teleport_requests` includes fast follow and spawn teleports as well
as hard corrections; `hard_corrections` retains the existing narrower statistic.
These are command requests, not acknowledgements of execution. The teleport
age resets to -1 when the session or avatar generation changes; the request
counter remains cumulative for this trace. A queued teleport
can apply later; its counter alone does not prove a later interval is clean.
Compare `teleport_age_s`, visible jumps and the gameplay recording before treating
an interval as normal locomotion. Vehicle parking and missing avatars produce
gaps, not zero-speed samples. `commands_started` counts real AIMoveTo sends;
`retargets` counts accepted target writes, not proof that AI consumed each write.

Five-second STATS remain useful for coarse comparisons. This optional trace
adds temporal detail without changing movement or adding prediction. It does
not establish that the test route is unobstructed or validate the 12-second A10
fixture when the regular bot still sprints for only three seconds.
