#!/usr/bin/env bash
# Compile-checks the npcsync redscript prototype against the vanilla 2.31 script cache,
# Codeware and the current CP2077Coop scripts, using the popup-free scc_check.py.
# Copies into sccgame/r6/scripts/CP2077Coop_net2/npcsync and removes that copy afterwards
# (CP2077Coop_net2 itself is removed only when it is left empty).
set -u
HERE="$(cd "$(dirname "$0")/.." && pwd)"
SCRATCH="$(cd "$HERE/../.." && pwd)"
GAME="$SCRATCH/sccgame"
TARGET_PARENT="$GAME/r6/scripts/CP2077Coop_net2"
TARGET="$TARGET_PARENT/npcsync"
CHECKER="D:/Downloads/syncfix/MP=Jakub/coop-tools/scc_check.py"

mkdir -p "$TARGET"
cp "$HERE"/r6/scripts/CP2077Coop_npcsync/*.reds "$TARGET"/
python "$CHECKER" "$GAME"
status=$?
rm -rf "$TARGET"
rmdir "$TARGET_PARENT" 2>/dev/null
exit $status
