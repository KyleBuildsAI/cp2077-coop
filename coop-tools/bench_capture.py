"""Capture live bench evidence without controlling a game or modifying its files.

Usage: python coop-tools/bench_capture.py HOST_GAME JOINER_GAME --out RUN_FOLDER
Latest stats and the bounded event logs are sampled into JSONL; original logs are
copied when capture ends. A timestamp is a capture time, not a network sample time.
"""
import argparse
import hashlib
import json
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

MOD = Path("bin/x64/plugins/cyber_engine_tweaks/mods/CP2077Coop")
FILES = [
    MOD / "coop_events.log",
    MOD / "coop_stats_host.txt",
    MOD / "coop_stats_joiner.txt",
    MOD / "CP2077Coop.log",
    Path("bin/x64/plugins/cyber_engine_tweaks/scripting.log"),
    Path("bin/x64/plugins/cyber_engine_tweaks/gamelog.log"),
    Path("bin/x64/plugins/cyber_engine_tweaks/mods/CoopNetCheck/coopnet_check.log"),
    Path("r6/logs/redscript_rCURRENT.log"),
    Path("red4ext/logs/red4ext.log"),
    Path("red4ext/plugins/CP2077CoopNet/CP2077CoopNet.log"),
]


def read_text(path):
    try:
        return path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None


def snapshot_sources(game):
    sources = [MOD / "init.lua", MOD / "net_transport.lua", MOD / "transport.ini",
               MOD / "role.txt", MOD / "testpattern.txt",
               MOD.parent / "CoopNetCheck/init.lua", MOD.parent / "CoopNetCheck/disabled.txt"]
    sources += [p.relative_to(game) for p in (game / "r6/scripts/CP2077Coop").glob("*.reds")]
    sources += [Path("red4ext/plugins/CP2077CoopNet/CP2077CoopNet.dll")]
    hashes = {}
    for relative in sources:
        path = game / relative
        if path.is_file():
            hashes[str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", type=Path)
    parser.add_argument("joiner", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--duration", type=float, default=300)
    parser.add_argument("--period", type=float, default=2)
    parser.add_argument("--label", default="manual bench; transport profile recorded separately")
    args = parser.parse_args()
    if args.duration <= 0 or args.period <= 0:
        parser.error("duration and period must be positive")
    games = {"host": args.host.resolve(), "joiner": args.joiner.resolve()}
    output = args.out.resolve()
    for game in games.values():
        if not (game / "bin/x64/Cyberpunk2077.exe").is_file():
            parser.error(f"not a game folder: {game}")
        if output == game or output.is_relative_to(game):
            parser.error("capture output must be outside the game folders")
    output.mkdir(parents=True, exist_ok=True)
    capture = output / "capture.jsonl"
    if capture.exists():
        parser.error("capture.jsonl already exists; choose a new run folder")
    manifest = {
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "label": args.label,
        "games": {role: {"path": str(game), "sha256": snapshot_sources(game),
                         "version": (re.findall(r'VERSION\s*=\s*"([^"]+)"', read_text(game / MOD / "init.lua") or "") or [None])[0]}
                  for role, game in games.items()},
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    prior = {}
    started = time.monotonic()
    report_at = started
    samples = 0
    try:
        with capture.open("w", encoding="utf-8") as handle:
            while time.monotonic() - started < args.duration:
                now = datetime.now(timezone.utc).isoformat()
                for role, game in games.items():
                    for relative in FILES:
                        if relative.name not in ("coop_events.log", f"coop_stats_{role}.txt", "coopnet_check.log"):
                            continue
                        content = read_text(game / relative)
                        key = (role, str(relative))
                        if content is not None and content != prior.get(key):
                            try:
                                modified_utc = datetime.fromtimestamp((game / relative).stat().st_mtime, timezone.utc).isoformat()
                            except OSError:
                                modified_utc = None
                            handle.write(json.dumps({"captured_utc": now, "role": role,
                                                     "modified_utc": modified_utc,
                                                     "file": str(relative), "text": content}) + "\n")
                            prior[key] = content
                            samples += 1
                handle.flush()
                if time.monotonic() >= report_at:
                    print(f"capture {time.monotonic() - started:.0f}s: {samples} changed file samples", flush=True)
                    report_at = time.monotonic() + 30
                time.sleep(min(args.period, max(0, args.duration - (time.monotonic() - started))))
    except KeyboardInterrupt:
        pass
    finally:
        for role, game in games.items():
            archive_files = list(FILES)
            for pattern in ("red4ext-*.log", "cp2077coopnet-*.log"):
                candidates = sorted((game / "red4ext/logs").glob(pattern), key=lambda p: p.stat().st_mtime)
                if candidates:
                    archive_files.append(candidates[-1].relative_to(game))
            for relative in archive_files:
                source = game / relative
                if source.is_file():
                    destination = output / role / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        shutil.copy2(source, destination)
                    except OSError as error:
                        print(f"could not copy {source}: {error}", flush=True)
        manifest["finished_utc"] = datetime.now(timezone.utc).isoformat()
        manifest["changed_file_samples"] = samples
        (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"evidence saved: {output}", flush=True)


if __name__ == "__main__":
    main()
