"""Summarize bench_capture STATS windows without inventing frame-level percentiles."""
import argparse
import json
import re
import statistics
from pathlib import Path


def summarize(folder, expected_version=None, probe_disabled=False):
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    roles = {name: {"windows": [], "probe": ""} for name in manifest["games"]}
    for line in (folder / "capture.jsonl").read_text(encoding="utf-8").splitlines():
        sample = json.loads(line)
        if sample.get("modified_utc") and sample["modified_utc"] < manifest["started_utc"]:
            continue
        role = roles[sample["role"]]
        if sample["file"].endswith(f"coop_stats_{sample['role']}.txt"):
            window = dict(re.findall(r"(\w+)=([^\s]+)", sample["text"]))
            version = expected_version or manifest["games"][sample["role"]].get("version")
            if version is None or window.get("version") == version:
                role["windows"].append(window)
        elif not probe_disabled and sample["file"].endswith("coopnet_check.log"):
            role["probe"] = sample["text"]
    report = {
        "measurement": "Aggregates of changed five-second STATS windows; not per-frame render-error percentiles. Probe counters can begin before capture.",
        "label": manifest["label"],
        "started_utc": manifest["started_utc"],
        "finished_utc": manifest.get("finished_utc"),
        "expected_version": expected_version,
        "probe_disabled": probe_disabled,
        "roles": {},
    }
    fields = ("rtt_ms", "fps", "avatar_err_m", "drift_avg_m", "drift_max_m",
              "hard_per_min", "flags_rx_ps", "missed_pct")
    for name, role in roles.items():
        windows = role["windows"]
        active = [s for s in windows if s.get("state") == "OK" and s.get("sync") == "on" and s.get("role") == name]
        result = {"windows": len(windows), "connected_windows": len(active),
                  "states": sorted({s.get("state", "unknown") for s in windows}),
                  "observed_roles": sorted({s.get("role", "unknown") for s in windows})}
        for field in fields:
            values = []
            for sample in active:
                try:
                    value = float(sample[field])
                    if value >= 0:
                        values.append(value)
                except (KeyError, ValueError):
                    pass
            result[field] = {"median": statistics.median(values), "max": max(values),
                             "samples": len(values)} if values else None
        result["counters"] = {}
        for field in ("commands_started", "retargets"):
            values = [int(s[field]) for s in active if s.get(field, "").isdigit()]
            if values:
                result["counters"][field] = {
                    "first": values[0], "last": values[-1],
                    "observed_increase": sum(b-a if b >= a else b for a,b in zip(values, values[1:])),
                    "resets": sum(b < a for a,b in zip(values, values[1:])),
                }
        probe_lines = [line for line in role["probe"].splitlines() if " REPORT " in line]
        if probe_lines:
            result["probe_last"] = dict(re.findall(r"(\w+)=([^\s]+)", probe_lines[-1].split(" stats=", 1)[0]))
        report["roles"][name] = result
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--version", help="exclude STATS left by an older deployment")
    parser.add_argument("--probe-disabled", action="store_true", help="exclude retained standalone probe logs")
    args = parser.parse_args()
    output = args.folder / "summary.json"
    output.write_text(json.dumps(summarize(args.folder, args.version, args.probe_disabled), indent=2), encoding="utf-8")
    print(output)
