#!/usr/bin/env bash
# Real CB77 codec/reliability/clock/interpolation + Python relay checks; no game assets.
# COOP_SANITIZE=ON COOP_BUILD_TYPE=Debug bash scripts/build-reference.sh build/sanitizers
set -euo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
build_dir="${1:-$repo_root/build/portable-linux}"
if [[ "$build_dir" != /* ]]; then build_dir="$repo_root/$build_dir"; fi
cmake -S "$repo_root" -B "$build_dir" -G Ninja \
    -DCMAKE_BUILD_TYPE="${COOP_BUILD_TYPE:-Release}" -DBUILD_TESTING=ON \
    -DCOOP_REFERENCE_ONLY=ON -DCOOP_BUILD_NATIVE_PLUGIN=OFF -DCOOP_ENABLE_SANITIZERS="${COOP_SANITIZE:-OFF}"
cmake --build "$build_dir" --parallel
ctest --test-dir "$build_dir" --output-on-failure --no-tests=error
