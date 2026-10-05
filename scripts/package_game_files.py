"""Build the pinned complete installation batch without touching a game installation."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "game-files/latest"
ARCHIVE = "CP2077Coop-v0.0.37-alpha5-game-files.zip"


def safe_relative(value: str) -> Path:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(p in ("..", ".") or ":" in p or "\\" in p for p in path.parts):
        raise ValueError(f"Unsafe manifest path: {value!r}")
    return Path(*path.parts)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build(bundle: Path, native: Path, output: Path) -> None:
    manifest_data = (SPEC / "package-manifest.json").read_bytes()
    manifest = json.loads(manifest_data)
    sources = {"repository": ROOT, "bundle": bundle.resolve(), "package": SPEC}
    payload = {}
    for entry in manifest["files"]:
        target = safe_relative(entry["path"])
        if entry["source"] == "native_binary":
            source = native.resolve()
        else:
            base = sources[entry["source"]]
            source = (base / safe_relative(entry["source_path"])).resolve()
            if not source.is_relative_to(base):
                raise ValueError(f"Source escapes its root: {entry['source_path']}")
        data = source.read_bytes()
        # Preserve the tested text bytes across Git checkouts with different EOL defaults.
        if entry.get("line_endings"):
            data = data.replace(b"\r\n", b"\n")
            if entry["line_endings"] == "crlf":
                data = data.replace(b"\n", b"\r\n")
        if len(data) != entry["bytes"] or digest(data) != entry["sha256"]:
            raise ValueError(f"Input does not match the tested package: {entry['path']}")
        if target in payload:
            raise ValueError(f"Duplicate output: {target}")
        payload[target] = data
    payload[Path("package-manifest.json")] = manifest_data
    checksums = "".join(f"{digest(data)}  {path.as_posix()}\n" for path, data in sorted(payload.items()))
    payload[Path("SHA256SUMS.txt")] = checksums.encode("utf-8")
    output = output.resolve()
    archive = output / ARCHIVE
    archive_checksum = output / (ARCHIVE + ".sha256")
    if archive.is_symlink() or archive_checksum.is_symlink():
        raise ValueError("Archive/checksum paths must not be symlinks")
    # Never turn a user's game folder or a symlinked output into a packaging target.
    if (output / "bin/x64/Cyberpunk2077.exe").exists() or (output / "game-root/bin/x64/Cyberpunk2077.exe").exists():
        raise ValueError("Output must be a package folder, not an installed game")
    expected = set(payload)
    for folder in ("game-root", "relay"):
        existing = output / folder
        if existing.exists():
            for path in existing.rglob("*"):
                if path.is_symlink() or not path.resolve().is_relative_to(output):
                    raise ValueError(f"Unsafe output link: {path}")
                if path.is_file() and path.relative_to(output) not in expected:
                    raise ValueError(f"Unexpected existing file in generated output: {path}")
    for relative, data in payload.items():
        target = output / relative
        if not target.resolve().is_relative_to(output):
            raise ValueError(f"Output escapes package: {relative}")
        if target.exists() and target.read_bytes() != data:
            raise ValueError(f"Existing file differs; use a new output folder: {target}")
    for relative, data in payload.items():
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    # Fixed ZIP metadata makes repeated packaging of this manifest byte-reproducible.
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as package:
        for relative, data in sorted(payload.items()):
            info = zipfile.ZipInfo(relative.as_posix(), (2026, 10, 4, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            package.writestr(info, data)
    with zipfile.ZipFile(archive) as package:
        if set(package.namelist()) != {p.as_posix() for p in payload} or package.testzip() is not None:
            raise ValueError("Archive file list or CRC verification failed")
        for relative, data in payload.items():
            if digest(package.read(relative.as_posix())) != digest(data):
                raise ValueError(f"Archive hash mismatch: {relative}")
    archive_checksum.write_text(f"{digest(archive.read_bytes())}  {ARCHIVE}\n", encoding="utf-8")
    print(f"Verified {len(manifest['files'])} payload files; {archive.stat().st_size:,} byte ZIP: {archive}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--native-plugin", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=SPEC)
    args = parser.parse_args()
    build(args.bundle_root, args.native_plugin, args.output)
