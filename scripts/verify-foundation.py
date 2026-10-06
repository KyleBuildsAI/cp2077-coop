"""Check the pinned upstream import and retained prototype against Git blob IDs.

Uses checkout filters so LF/CRLF differences are handled exactly as Git handles them.
Does not fetch, modify files, or require the source repositories to be reachable.
"""
from pathlib import Path, PurePosixPath
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    manifest = json.loads((ROOT / "docs/foundation-import.json").read_text(encoding="utf-8"))
    failures = []
    counts = {}
    for group in ("upstream_files", "reference_files", "proposal_files"):
        counts[group] = 0
        for name, expected in manifest[group].items():
            relative = PurePosixPath(name)
            if relative.is_absolute() or ".." in relative.parts or "\\" in name or ":" in name:
                raise ValueError(f"Unsafe import path: {name}")
            path = ROOT / name
            if not path.is_file() or not path.resolve().is_relative_to(ROOT):
                failures.append(f"Missing or external file: {name}")
                continue
            actual = subprocess.check_output(
                ["git", "hash-object", f"--path={name}", name], cwd=ROOT, text=True
            ).strip()
            if actual != expected:
                failures.append(f"Changed {name}: expected {expected}, got {actual}")
            counts[group] += 1
    for failure in failures:
        print(failure, file=sys.stderr)
    if failures:
        print("Review intentional ports and update the import record explicitly.", file=sys.stderr)
        return 1
    print("Foundation parity verified: " + ", ".join(f"{value} {key}" for key, value in counts.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
