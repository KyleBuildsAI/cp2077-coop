"""Optional actual-avatar CSV: real update hook, timing bounds and lifecycle."""
import contextlib
import csv
import os
from pathlib import Path
import sys
import tempfile

import test_v2_integration as integration


@contextlib.contextmanager
def fixture(enabled=True, **options):
    previous = os.getcwd()
    with tempfile.TemporaryDirectory(prefix="coop_trace_") as directory:
        os.chdir(directory)
        try:
            if enabled:
                Path("steering-trace.txt").touch()
            lua = integration.receiver(**options)
            values = [integration.upvalue(lua, name) for name in ("Diag", "Sync", "S")]
            yield (lua, *values)
        finally:
            os.chdir(previous)


def rows(role="host"):
    with open(f"coop_steering_{role}.csv", newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def sample(lua, diag, sync, state, now, x=4.0):
    sync.clock = now
    state.previousRemoteX, state.previousRemoteY, state.previousRemoteZ = 10, 2, 3
    state.targetX, state.targetY, state.targetZ = 8, 2, 3
    state.remoteVelocityX, state.remoteVelocityY, state.remoteVelocityZ = 3, 0, 0
    diag.recordDrift(lua.table_from({"x": x, "y": 2, "z": 3}))


def test_disabled_and_gate_checked_once():
    with fixture(enabled=False) as (lua, diag, sync, state):
        Path("steering-trace.txt").touch()  # a running game does not silently turn it on
        for index in range(180):
            sample(lua, diag, sync, state, index / 60)
        lua.globals().events["onShutdown"]()
        assert not list(Path.cwd().glob("coop_steering_*.csv"))
        assert diag.steeringTrace.rows == 0


def test_real_receive_hook_keeps_avatar_target_and_raw_distinct():
    with fixture(config="native_retarget=true\n") as (lua, diag, sync, state):
        integration.welcome(lua)
        for sequence in range(1, 181):
            integration.movement(lua, sequence)
            integration.frame(lua)
        assert diag.steeringTrace.rows > 0  # real onUpdate, not just direct diagnostic calls
        sample(lua, diag, sync, state, sync.clock + 0.2, x=4)
        lua.globals().events["onShutdown"]()
        last = rows()[-1]
        assert float(last["avatar_x"]) == 4 and float(last["target_x"]) == 8
        assert float(last["raw_x"]) == 10 and float(last["velocity_x"]) == 3
        assert float(last["target_error_m"]) == 4
        assert last["transport"] == "v2" and last["retained"] == "1"
        assert last["sample_mode"] == "interpolated" and last["source"] == "player"
        assert None not in last and len(last) == 31


def test_cadence_no_backfill_and_row_duration_caps():
    with fixture() as (lua, diag, sync, state):
        for index in range(180):
            sample(lua, diag, sync, state, index / 60)
        assert diag.steeringTrace.rows == 30
        sample(lua, diag, sync, state, 9.0)  # one observation after a six-second hitch
        assert diag.steeringTrace.rows == 31
        diag.flushSteeringTrace()
        data = rows()
        times = [float(row["t_s"]) for row in data]
        assert all(b - a >= 0.099999 for a, b in zip(times, times[1:]))
        assert times[-1] - times[-2] > 6
        sample(lua, diag, sync, state, 601)
        assert not diag.steeringTrace.enabled and len(rows()) == 31
    with fixture() as (lua, diag, sync, state):
        for index in range(7000):
            sample(lua, diag, sync, state, index / 10)
        assert diag.steeringTrace.rows == 6000
        assert not diag.steeringTrace.enabled and len(rows()) == 6000
        assert len(diag.steeringTrace.buffer) == 0


def test_correction_generation_and_reset_preserve_boundaries():
    with fixture() as (lua, diag, sync, state):
        sample(lua, diag, sync, state, 10)
        hard = integration.upvalue(lua, "hardCorrectRemote")
        hard(lua.globals().player, 8, 2, 3, False)  # fast-follow / spawn: still a teleport
        sample(lua, diag, sync, state, 10.2)
        hard(lua.globals().player, 8, 2, 3, True)
        sample(lua, diag, sync, state, 10.4)
        diag.resetSession()  # flush partial batch before hard counter resets
        sync.dropAvatar(lua.globals().player, "trace fixture")
        sample(lua, diag, sync, state, 10.6)
        lua.globals().events["onShutdown"]()
        data = rows()
        assert [int(row["teleport_requests"]) for row in data] == [0, 1, 2, 2]
        assert [int(row["hard_corrections"]) for row in data] == [0, 0, 1, 0]
        assert int(data[-1]["session"]) > int(data[-2]["session"])
        assert int(data[-1]["avatar_generation"]) > int(data[-2]["avatar_generation"])
        assert abs(float(data[1]["teleport_age_s"]) - 0.2) < 1e-6
        assert float(data[-1]["teleport_age_s"]) == -1  # previous avatar's request is not its own
        assert not diag.steeringTrace.enabled
        assert len(data) == 4 and len(diag.steeringTrace.buffer) == 0


def test_partial_flush_when_avatar_disappears_and_write_failure_isolated():
    with fixture() as (lua, diag, sync, state):
        sample(lua, diag, sync, state, 1)
        assert not Path("coop_steering_host.csv").exists()
        sync.clock = 2.1
        diag.tick(0.1)  # no avatar observations, but a partial batch is still saved
        assert len(rows()) == 1
        # An unwritable output must disable only tracing, never the receive loop.
        Path("coop_steering_host.csv").unlink()
        Path("coop_steering_host.csv").mkdir()
        sample(lua, diag, sync, state, 3)
        diag.flushSteeringTrace()
        assert not diag.steeringTrace.enabled and len(diag.steeringTrace.buffer) == 0
        sample(lua, diag, sync, state, 4, x=5)
        assert diag.avatarError == 3  # ordinary diagnostics still run


def test_clone_skips_gate_and_output():
    package = Path(integration.harness.SCRIPT).parents[6]
    sys.path.insert(0, str(package / "coop-tools"))
    import devkit
    assert all(devkit.is_skipped(name) for name in (
        "steering-trace.txt", "coop_steering_host.csv", "coop_steering_joiner.csv"))
    assert not devkit.is_skipped("init.lua")


if __name__ == "__main__":
    failed = 0
    for name, function in sorted(globals().copy().items()):
        if name.startswith("test_") and callable(function):
            try:
                function()
                print(f"PASS {name}")
            except Exception as error:
                import traceback
                traceback.print_exc()
                print(f"FAIL {name}: {error}")
                failed += 1
    sys.exit(bool(failed))
