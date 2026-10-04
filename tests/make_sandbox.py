"""Build a redscript compile sandbox in %TEMP% for coop-tools/scc_check.py.

The sandbox is a minimal fake game folder: the compiler (engine/tools), the
vanilla script cache (r6/cache/final.redscripts) and Codeware's scripts
(red4ext/plugins/Codeware/Scripts) are copied from a real game folder, which
is only ever read. The repo's r6/scripts/CP2077Coop/*.reds are then mirrored
in, so the sandbox always compiles the current checkout.

Game folder: --game, else the COOP_GAME_DIR environment variable, else
DEFAULT_GAME_DIR. The sandbox path is printed as the last line of output.

Usage:
    python tests/make_sandbox.py
    python tests/make_sandbox.py --game "D:/Games/Cyberpunk 2077" --out D:/tmp/sccbox

Exit code 0 = sandbox ready, 2 = setup problem (missing or unsafe folder).
"""
import argparse
import os
import shutil
import sys
import tempfile

DEFAULT_GAME_DIR = r"G:\SteamLibrary\steamapps\common\Cyberpunk 2077 - Test B"
SANDBOX_NAME = "cp2077coop_scc_sandbox"
MARKER = ".cp2077coop_sandbox"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOD_SCRIPTS = os.path.join("r6", "scripts", "CP2077Coop")

# (relative path in the game folder, required) - the only things read from it
TOOLS_DIR = os.path.join("engine", "tools")
VANILLA_CACHE = os.path.join("r6", "cache", "final.redscripts")
CODEWARE_SCRIPTS = os.path.join("red4ext", "plugins", "Codeware", "Scripts")


class SetupError(Exception):
    pass


def is_inside(path, folder):
    path, folder = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(folder))
    try:
        return os.path.commonpath([path, folder]) == folder
    except ValueError:  # different drives
        return False


def same_file(source, target):
    if not os.path.isfile(target):
        return False
    source_stat, target_stat = os.stat(source), os.stat(target)
    return source_stat.st_size == target_stat.st_size and int(source_stat.st_mtime) == int(target_stat.st_mtime)


def copy_file(source, target):
    """Copy one file unless an identical copy (size + mtime) is already there. Returns True if copied."""
    if same_file(source, target):
        return False
    os.makedirs(os.path.dirname(target), exist_ok=True)
    shutil.copy2(source, target)
    return True


def mirror_files(source_dir, target_dir, names):
    """Make target_dir hold exactly `names` from source_dir. Returns the number of files written or removed."""
    os.makedirs(target_dir, exist_ok=True)
    changes = 0
    wanted = set(names)
    for name in os.listdir(target_dir):
        stale = os.path.join(target_dir, name)
        if name not in wanted and os.path.isfile(stale):
            os.remove(stale)
            changes += 1
    for name in names:
        changes += copy_file(os.path.join(source_dir, name), os.path.join(target_dir, name))
    return changes


def files_in(folder, suffix=""):
    return sorted(name for name in os.listdir(folder)
                  if os.path.isfile(os.path.join(folder, name)) and name.lower().endswith(suffix))


def prepare_sandbox_dir(sandbox, game):
    if is_inside(sandbox, game) or is_inside(game, sandbox):
        raise SetupError(f"sandbox {sandbox} must not overlap the game folder {game}")
    if is_inside(sandbox, REPO):
        raise SetupError(f"sandbox {sandbox} must be outside the repo {REPO}")
    marker = os.path.join(sandbox, MARKER)
    if os.path.isdir(sandbox) and os.listdir(sandbox) and not os.path.isfile(marker):
        raise SetupError(f"{sandbox} exists and is not a sandbox made by this script; pick another --out")
    os.makedirs(sandbox, exist_ok=True)
    with open(marker, "w", encoding="utf-8") as handle:
        handle.write(f"redscript compile sandbox built from {game}\n")


def build(game, sandbox):
    game, sandbox = os.path.abspath(game), os.path.abspath(sandbox)
    if not os.path.isdir(game):
        raise SetupError(f"game folder not found: {game} (set COOP_GAME_DIR or pass --game)")
    for required in (os.path.join(TOOLS_DIR, "scc_lib.dll"), VANILLA_CACHE):
        if not os.path.isfile(os.path.join(game, required)):
            raise SetupError(f"game folder has no {required}: {game}")
    prepare_sandbox_dir(sandbox, game)

    tools = os.path.join(game, TOOLS_DIR)
    changes = mirror_files(tools, os.path.join(sandbox, TOOLS_DIR), files_in(tools))
    changes += copy_file(os.path.join(game, VANILLA_CACHE), os.path.join(sandbox, VANILLA_CACHE))

    codeware = os.path.join(game, CODEWARE_SCRIPTS)
    if os.path.isdir(codeware):
        changes += mirror_files(codeware, os.path.join(sandbox, CODEWARE_SCRIPTS), files_in(codeware, ".reds"))
    else:
        print(f"note: no Codeware in {game}; @if(ModuleExists(\"Codeware\")) blocks will not be compiled")
        stale = os.path.join(sandbox, CODEWARE_SCRIPTS)
        if os.path.isdir(stale):
            changes += mirror_files(stale, stale, [])

    mod_scripts = os.path.join(REPO, MOD_SCRIPTS)
    changes += mirror_files(mod_scripts, os.path.join(sandbox, MOD_SCRIPTS), files_in(mod_scripts, ".reds"))
    print(f"sandbox from {game}: {changes} file(s) updated")
    return sandbox


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--game", default=os.environ.get("COOP_GAME_DIR") or DEFAULT_GAME_DIR,
                        help="game folder to copy the compiler and caches from (read only)")
    parser.add_argument("--out", default=os.path.join(tempfile.gettempdir(), SANDBOX_NAME),
                        help="sandbox folder (default: %%TEMP%%\\" + SANDBOX_NAME + ")")
    args = parser.parse_args()
    try:
        print(build(args.game, args.out))
    except (SetupError, OSError) as error:
        print(f"make_sandbox: {error}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
