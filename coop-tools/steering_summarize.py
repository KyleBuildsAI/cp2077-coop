"""Summarize observed avatar traces; target error is not present-time network truth."""
import argparse
from collections import Counter
import csv
from datetime import datetime
import json
import math
from pathlib import Path
import statistics


FLOAT_FIELDS = ("wall_unix_s", "t_s", "avatar_x", "avatar_y", "avatar_z",
                "target_x", "target_y", "target_z", "remote_speed", "teleport_age_s")
COUNTERS = ("commands_started", "retargets", "hard_corrections", "teleport_requests")
INT_FIELDS = ("session", "avatar_generation", "remote_flags", "retained", *COUNTERS)
TEXT_FIELDS = ("transport", "source", "sample_mode")
THRESHOLDS = {"idle_max_mps": 0.1, "run_min_mps": 2.6,
              "sprint_min_mps": 5.5, "fast_follow_min_mps": 8.0}


def distribution(values):
    if not values:
        return {"samples": 0, "p50": None, "p95": None, "mean": None, "max": None}
    ordered = sorted(values)

    def percentile(fraction):
        index = (len(ordered) - 1) * fraction
        low = math.floor(index)
        high = min(low + 1, len(ordered) - 1)
        return ordered[low] + (ordered[high] - ordered[low]) * (index - low)

    return {"samples": len(values), "p50": percentile(0.5), "p95": percentile(0.95),
            "mean": statistics.fmean(values), "max": ordered[-1]}


def motion(row):
    speed, flags = row["remote_speed"], row["remote_flags"]
    if flags & 16:
        return "vehicle"
    if row["sample_mode"] == "held":
        return "held"
    if flags & 1:
        return "crouch_idle" if speed <= 0.1 else "crouch"
    if speed <= 0.1:
        return "idle"
    if speed >= 8.0:
        return "fast_follow"
    return "sprint" if speed >= 5.5 else ("run" if speed >= 2.6 else "walk")


def read_rows(path, start_unix=None, end_unix=None):
    result, invalid, outside = [], 0, 0
    filtered_gap = False
    with path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        missing = set((*FLOAT_FIELDS, *INT_FIELDS, *TEXT_FIELDS)) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path}: missing columns: {', '.join(sorted(missing))}")
        for raw in reader:
            try:
                row = {key: float(raw[key]) for key in FLOAT_FIELDS}
                row.update({key: int(raw[key]) for key in INT_FIELDS})
                row.update({key: raw[key] for key in TEXT_FIELDS})
                if (None in raw or not all(math.isfinite(row[key]) for key in FLOAT_FIELDS)
                        or any(row[key] < 0 for key in INT_FIELDS) or row["remote_speed"] < 0
                        or (row["teleport_age_s"] < 0 and row["teleport_age_s"] != -1)
                        or any(not row[key] for key in TEXT_FIELDS)):
                    raise ValueError("invalid or partial row")
            except (ValueError, TypeError, KeyError):
                invalid += 1
                result.append(None)  # never bridge a corrupt/missing observation
                continue
            if ((start_unix is not None and row["wall_unix_s"] < start_unix)
                    or (end_unix is not None and row["wall_unix_s"] > end_unix)):
                outside += 1
                filtered_gap = True
                continue
            if filtered_gap and result:
                result.append(None)  # an excluded observation also breaks continuity
            filtered_gap = False
            row["motion"] = motion(row)
            row["target_error"] = math.dist(
                [row[f"avatar_{axis}"] for axis in "xyz"],
                [row[f"target_{axis}"] for axis in "xyz"])
            result.append(row)
    return result, invalid, outside


def interval_rejection(a, b, max_gap_s, teleport_exclusion_s):
    if a is None or b is None:
        return "missing_or_invalid_endpoint"
    if a["session"] != b["session"] or a["avatar_generation"] != b["avatar_generation"]:
        return "session_or_avatar_changed"
    dt = b["t_s"] - a["t_s"]
    if dt <= 0:
        return "nonincreasing_time"
    if dt > max_gap_s + 1e-9:
        return "observation_gap"
    if any(a[key] != b[key] for key in ("transport", "retained", "source")):
        return "transport_config_or_source_changed"
    if a["teleport_requests"] != b["teleport_requests"]:
        return "teleport_request_changed"
    if any(0 <= row["teleport_age_s"] < teleport_exclusion_s for row in (a, b)):
        return "recent_teleport_endpoint"
    if a["motion"] != b["motion"]:
        return "motion_group_changed"
    return None


def summarize(path, max_gap_s=0.25, teleport_exclusion_s=0.5, start_unix=None, end_unix=None):
    if not all(math.isfinite(value) for value in (max_gap_s, teleport_exclusion_s)) or max_gap_s <= 0 or teleport_exclusion_s < 0:
        raise ValueError("max gap must be positive; teleport exclusion must be nonnegative")
    if any(value is not None and not math.isfinite(value) for value in (start_unix, end_unix)):
        raise ValueError("wall-time bounds must be finite")
    if start_unix is not None and end_unix is not None and start_unix > end_unix:
        raise ValueError("start must precede end")
    rows, invalid, outside = read_rows(Path(path), start_unix, end_unix)
    valid = [row for row in rows if row is not None]
    groups = {}
    for row in valid:
        groups.setdefault(row["motion"], {"all_error": [], "eligible": set(), "intervals": []})["all_error"].append(row["target_error"])
    rejected = Counter()
    for index, (a, b) in enumerate(zip(rows, rows[1:])):
        reason = interval_rejection(a, b, max_gap_s, teleport_exclusion_s)
        if reason:
            rejected[reason] += 1
            continue
        dt = b["t_s"] - a["t_s"]
        displacement = math.dist([a[f"avatar_{axis}"] for axis in "xyz"],
                                 [b[f"avatar_{axis}"] for axis in "xyz"])
        speed_xy = math.hypot(b["avatar_x"] - a["avatar_x"], b["avatar_y"] - a["avatar_y"]) / dt
        group = groups[b["motion"]]
        group["eligible"].update((index, index + 1))
        group["intervals"].append((dt, displacement, speed_xy))
    by_motion = {}
    for name, group in sorted(groups.items()):
        intervals = group["intervals"]
        by_motion[name] = {
            "observed_target_error_m": distribution(group["all_error"]),
            "eligible_target_error_m": distribution([rows[index]["target_error"] for index in group["eligible"]]),
            "accepted_intervals": len(intervals), "accepted_seconds": sum(v[0] for v in intervals),
            "avatar_displacement_m": distribution([v[1] for v in intervals]),
            "avatar_speed_xy_mps": distribution([v[2] for v in intervals]),
        }
    counters = {}
    for key in COUNTERS:
        values = [row[key] for row in valid]
        counters[key] = ({"first": values[0], "last": values[-1],
                          "observed_increase": sum(b - a if b >= a else b for a, b in zip(values, values[1:])),
                          "observed_resets": sum(b < a for a, b in zip(values, values[1:]))} if values else None)
    return {
        "file": str(Path(path).resolve()),
        "measurement": "Observed avatar-to-steering-target distance; v2 target is a buffered sample, not present-time/network truth. Displacement speeds use eligible observed intervals, not animation frames.",
        "limitations": "Teleport exclusion uses request age, not execution acknowledgement; delayed execution can remain. Geometry, frame rate and route can affect results. No causal improvement is inferred. Counters exclude activity before the first row and can miss increments around resets.",
        "filters": {"max_gap_s": max_gap_s, "teleport_endpoint_exclusion_s": teleport_exclusion_s,
                    "wall_start_unix_s": start_unix, "wall_end_unix_s": end_unix,
                    "wall_time_resolution_s": 1, "motion_thresholds": THRESHOLDS,
                    "percentiles": "linear interpolation of observed values; not time weighted"},
        "valid_rows": len(valid), "invalid_rows": invalid, "rows_outside_window": outside,
        "observed_span_s": valid[-1]["t_s"] - valid[0]["t_s"] if valid else None,
        "retained_values": sorted({row["retained"] for row in valid}),
        "candidate_intervals": max(0, len(rows) - 1),
        "accepted_intervals": sum(group["accepted_intervals"] for group in by_motion.values()),
        "rejected_intervals": dict(sorted(rejected.items())),
        "observed_target_error_m": distribution([row["target_error"] for row in valid]),
        "counters": counters, "by_motion": by_motion,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="+", type=Path, help="CSV files or capture folders (manifest bounds applied)")
    parser.add_argument("--output", type=Path, help="write JSON here; default stdout")
    parser.add_argument("--max-gap", type=float, default=0.25)
    parser.add_argument("--teleport-exclusion", type=float, default=0.5)
    parser.add_argument("--start-unix", type=float, help="override capture start bound")
    parser.add_argument("--end-unix", type=float, help="override capture end bound")
    args = parser.parse_args()
    reports = []
    try:
        for entry in args.input:
            start, end = args.start_unix, args.end_unix
            files = sorted(entry.rglob("coop_steering_*.csv")) if entry.is_dir() else [entry]
            manifest = entry / "manifest.json"
            if entry.is_dir() and manifest.is_file():
                metadata = json.loads(manifest.read_text(encoding="utf-8-sig"))
                if start is None and metadata.get("started_utc"):
                    start = math.ceil(datetime.fromisoformat(metadata["started_utc"]).timestamp())
                if end is None and metadata.get("finished_utc"):
                    # A wall timestamp k represents any time in [k, k+1).
                    # Keep only complete one-second bins inside the capture.
                    end = math.floor(datetime.fromisoformat(metadata["finished_utc"]).timestamp()) - 1
            if not files:
                raise ValueError(f"{entry}: no steering CSV files")
            reports.extend(summarize(path, args.max_gap, args.teleport_exclusion, start, end) for path in files)
    except (OSError, ValueError, csv.Error) as error:
        parser.error(str(error))
    output = json.dumps({"traces": reports}, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.write_text(output, encoding="utf-8")
        print(args.output)
    else:
        print(output, end="")


if __name__ == "__main__":
    main()
