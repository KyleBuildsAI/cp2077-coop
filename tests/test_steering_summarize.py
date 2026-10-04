"""Observed displacement fixtures with discontinuities and delayed teleport exclusions."""
import csv
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile

TOOLS = Path(__file__).resolve().parents[1] / "coop-tools"
sys.path.insert(0, str(TOOLS))
import steering_summarize as summary


def row(time=0, x=0, **changes):
    value = {key: 0 for key in (*summary.FLOAT_FIELDS, *summary.INT_FIELDS)}
    value.update(wall_unix_s=1000 + int(time), t_s=time, avatar_x=x, target_x=x + 3,
                 transport="v2", source="bot", sample_mode="interpolated",
                 remote_speed=7, teleport_age_s=-1, local_bot_phase="pistol",
                 target_error_m=999)  # analyzer recomputes observed distance
    value.update(changes)
    return value


def write(path, data):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(data[0]))
        writer.writeheader()
        writer.writerows(data)


def test_observed_motion_discontinuities_and_both_teleport_endpoints():
    data = [row(0, 0), row(.1, .7), row(.2, 1.4), row(.3, 100, session=2),
            row(.4, 100.7, session=2), row(.8, 500, session=2)]
    for time, x, age in ((.9, 501, .1), (1, 1000, .2), (1.1, 1000.7, .6), (1.2, 1001.4, .7)):
        data.append(row(time, x, session=2, teleport_requests=1, teleport_age_s=age))
    for time, x, changes in ((1.3, 1002.1, {}), (1.4, 1002.8, {}),
                              (1.4, 1003.5, {}), (1.5, 1004.2, {"retained": 1}),
                              (1.6, 1004.9, {"retained": 1, "remote_speed": 0}),
                              (1.7, 1004.9, {"retained": 1, "remote_speed": 0})):
        data.append(row(time, x, session=2, avatar_generation=2, teleport_requests=1,
                        teleport_age_s=.8, **changes))
    for index, item in enumerate(data):
        item["commands_started"] = index
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "coop_steering_host.csv"
        write(path, data)
        report = summary.summarize(path)
    assert report["accepted_intervals"] == 6
    assert report["rejected_intervals"] == {
        "session_or_avatar_changed": 2, "observation_gap": 1,
        "teleport_request_changed": 1, "recent_teleport_endpoint": 2,
        "nonincreasing_time": 1, "transport_config_or_source_changed": 1,
        "motion_group_changed": 1}
    sprint = report["by_motion"]["sprint"]
    assert sprint["accepted_intervals"] == 5
    assert math.isclose(sprint["avatar_speed_xy_mps"]["p95"], 7, abs_tol=1e-8)
    assert sprint["observed_target_error_m"]["p50"] == 3
    assert report["by_motion"]["idle"]["avatar_speed_xy_mps"]["p95"] == 0
    assert report["counters"]["commands_started"]["observed_increase"] == 15
    assert report["filters"]["teleport_endpoint_exclusion_s"] == .5


def test_flags_speed_groups_and_exact_exclusion_boundary():
    assert [summary.motion(row(remote_speed=s)) for s in (0, .1, 1.8, 2.6, 5.5, 8)] == [
        "idle", "idle", "walk", "run", "sprint", "fast_follow"]
    assert summary.motion(row(remote_flags=16)) == "vehicle"
    assert summary.motion(row(remote_flags=1)) == "crouch"
    assert summary.motion(row(remote_flags=1, remote_speed=0)) == "crouch_idle"
    assert summary.motion(row(sample_mode="held")) == "held"
    a, b = row(0, teleport_age_s=.5), row(.25, teleport_age_s=.75)
    for value in (a, b):
        value["motion"] = summary.motion(value)
    assert summary.interval_rejection(a, b, .25, .5) is None
    a["teleport_age_s"] = .499
    assert summary.interval_rejection(a, b, .25, .5) == "recent_teleport_endpoint"


def test_invalid_row_breaks_continuity_and_counter_resets_reported():
    data = [row(0, hard_corrections=4), row(.1, hard_corrections=5),
            row(.15, avatar_x="nan"), row(.2, hard_corrections=0), row(.3, hard_corrections=2)]
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "coop_steering_host.csv"
        write(path, data)
        report = summary.summarize(path)
    assert report["invalid_rows"] == 1 and report["accepted_intervals"] == 2
    assert report["rejected_intervals"] == {"missing_or_invalid_endpoint": 2}
    assert report["counters"]["hard_corrections"]["observed_increase"] == 3
    assert report["counters"]["hard_corrections"]["observed_resets"] == 1


def test_negative_age_and_excluded_rows_do_not_create_eligible_intervals():
    data = [row(0), row(.05, teleport_age_s=-.2), row(.1),
            row(.15, wall_unix_s=999), row(.2), row(.3)]
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "coop_steering_host.csv"
        write(path, data)
        report = summary.summarize(path, start_unix=1000)
    assert report["invalid_rows"] == 1 and report["rows_outside_window"] == 1
    assert report["accepted_intervals"] == 1
    assert report["rejected_intervals"] == {"missing_or_invalid_endpoint": 4}


def test_capture_cli_uses_conservative_manifest_bounds():
    with tempfile.TemporaryDirectory() as folder:
        folder = Path(folder)
        write(folder / "coop_steering_host.csv", [row(i / 10, i * .7) for i in range(40)])
        (folder / "manifest.json").write_text(json.dumps({
            "started_utc": "1970-01-01T00:16:40.500000+00:00",
            "finished_utc": "1970-01-01T00:16:43.500000+00:00"}), encoding="utf-8")
        output = folder / "steering-summary.json"
        run = subprocess.run([sys.executable, str(TOOLS / "steering_summarize.py"), str(folder),
                              "--output", str(output)], capture_output=True, text=True)
        assert run.returncode == 0, run.stderr
        report = json.loads(output.read_text(encoding="utf-8"))["traces"][0]
        assert report["valid_rows"] == 20 and report["rows_outside_window"] == 20
        assert report["filters"]["wall_start_unix_s"] == 1001
        assert report["filters"]["wall_end_unix_s"] == 1002
        assert report["accepted_intervals"] == 19


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
