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

## 2026-10-04 retained-command trial

The opt-in path ran successfully, but these measurements do **not** establish a
movement-fidelity improvement. Keep the source default `native_retarget=false`.
Retarget-on remains an experimental setting requiring a matched follow-up.

Evidence: `D:/Downloads/syncfix/bench-artifacts/20261004-codex/` contains
`v35-retarget-off` (runtime e00d3f2) and `v36-retarget-on` (runtime 0aa0159), each
with `summary.json` and `run-notes.md`; the on-run also has `steering-summary.json`
and archived CSVs. Each capture lasted 300 seconds with alpha.5, the local
95–135 ms / 1% loss relay profile, and no controlled NPC actor spawned. Both
had median 60 FPS and approximately 282 ms median RTT.

The off-run joiner bot retained anchor (-1837.23, 3855.16, 7.01), while the
on-run used (-1843.40, 3857.40, 6.86) for both bots. Thus the host's received
route differs. The host sender anchor matches, but builds, phase timing, camera
framing and trace availability also differ. The routes cross debris/warehouse
geometry. These are not a matched two-direction A/B experiment, a clear-lane
speed measurement, or the continuous 12-second A10 sprint fixture.

Five-second STATS comparisons, with host/joiner values paired:

| Metric | Off | On |
| --- | --- | --- |
| Observed AIMoveTo starts | 486 / 480 | 253 / 272 |
| Observed accepted retarget writes | 0 / 0 | 5842 / 5808 |
| Median window-mean target error (m) | 1.15 / 1.13 | 1.19 / 1.36 |
| Median window-maximum target error (m) | 2.66 / 2.81 | 2.99 / 3.22 |
| Worst window-maximum target error (m) | 6.46 / 6.36 | 6.65 / 6.61 |
| Median rolling hard-correction rate (/min) | 5 / 5 | 7 / 8 |

Command starts were 47.9% / 43.3% lower in the on-run; fewer starts and accepted
target writes do not prove that the AI continuously consumed those targets.
Error and correction statistics did not improve in this comparison.

The on-run trace contained 2363 / 2393 observations, with overall observed
target-error p50 1.52 / 1.47 m and p95 5.08 / 5.32 m. The default filters accepted
1969 / 1991 intervals. The table uses **all observed** errors for p95 and only
eligible intervals for displacement speed; these are different populations.

| Incoming motion group | Target-error p95 (m), host / joiner | Avatar horizontal speed p50 / p95 (m/s), host | Avatar horizontal speed p50 / p95 (m/s), joiner | Accepted intervals, host / joiner |
| --- | --- | --- | --- | --- |
| Idle | 3.46 / 3.48 | 0.07 / 2.65 | 0.04 / 2.50 | 682 / 668 |
| Walk | 4.63 / 4.42 | 1.25 / 2.06 | 1.24 / 1.94 | 353 / 343 |
| Run | 5.81 / 5.75 | 1.87 / 5.37 | 1.84 / 5.20 | 266 / 277 |
| Sprint | 5.91 / 5.81 | 4.75 / 5.93 | 4.64 / 5.99 | 86 / 97 |
| Crouch movement | 3.51 / 2.73 | 0.97 / 2.00 | 1.13 / 1.91 | 337 / 364 |
| Crouch idle | 2.64 / 2.22 | 0.04 / 1.98 | 0.03 / 1.98 | 245 / 242 |

Sprint intervals total only 9.38 / 10.65 seconds across repeated three-second
segments. Fast-follow and vehicle groups have no accepted displacement intervals.
Some accepted walk/idle/crouch intervals still reach 18–34 m/s: the 0.5-second
teleport-request-age exclusion does not prove that delayed teleports have finished.
Do not interpret these results as the avatar's engine speed cap. No corresponding
off-run trace exists, so no per-motion trace improvement can be calculated.

A useful next comparison needs the same build and inspected route, freshly
verified anchors, tracing on both sides, and one change: `native_retarget`.
The separate sustained sprint probe remains necessary for A10.
