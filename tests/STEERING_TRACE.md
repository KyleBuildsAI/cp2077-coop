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

## Offline summary

Run `python coop-tools/steering_summarize.py CAPTURE_FOLDER --output steering-summary.json`
or pass one or more CSV paths. Capture folders use their manifest's start/end
times to exclude startup or later interactions; because wall time is recorded
to one second, only complete second bins within those bounds are included.
Direct CSV input summarizes the entire file. `--start-unix` and `--end-unix`
override those bounds when needed.

The report gives p50/p95 observed target error and accepted-interval displacement
and horizontal avatar speed. Motion groups use incoming speed and flags, not the
local bot phase or the AI's catch-up gait. Idle/walk/run/sprint thresholds are
0.1/2.6/5.5 m/s; speed at least 8 m/s, crouch, vehicle and held samples are reported
separately. Each interval requires an unchanged session/avatar, transport/config,
source and motion group, increasing time, and no gap over 0.25 seconds. Changed
teleport request counts or either endpoint with teleport age below 0.5 seconds
exclude the interval. The report states both thresholds and rejection counts;
`--max-gap` and `--teleport-exclusion` can change them explicitly.

`observed_target_error_m` includes all valid observations, including corrections.
`eligible_target_error_m` uses unique endpoints of accepted intervals. These are
observed-sample percentiles, not per-frame or time-weighted statistics. The
filters cannot prove that delayed teleports or pathing effects are absent.
