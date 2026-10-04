"""make_sandbox.py copies the same plugin scripts the game compiles, from a fake game folder.

S1  every red4ext/plugins/*/Scripts folder is mirrored, subfolders included; our
    own CP2077Coop plugin folder is not (its scripts come from the repo), and a
    .reds file left there is reported
S2  a plugin removed from the game is removed from the sandbox on the next build
S3  a sandbox inside the game folder or inside the repo is refused

No compiler runs and no real game folder is touched. The runner passes the init.lua
path as the first argument; it is not used.

Usage: python test_make_sandbox.py [ignored]
"""
import contextlib
import io
import os
import shutil
import sys
import tempfile

import make_sandbox

PLUGINS = os.path.join("red4ext", "plugins")
FAKE_FILES = [
    os.path.join("engine", "tools", "scc_lib.dll"),
    os.path.join("r6", "cache", "final.redscripts"),
    os.path.join(PLUGINS, "Codeware", "Scripts", "Codeware.reds"),
    os.path.join(PLUGINS, "CP2077CoopNet", "Scripts", "Helpers.reds"),
    os.path.join(PLUGINS, "CP2077CoopNet", "Scripts", "sub", "Nested.reds"),
    os.path.join(PLUGINS, "CP2077Coop", "Scripts", "stale.reds"),
    os.path.join(PLUGINS, "CP2077Coop", "server.ini"),
    os.path.join(PLUGINS, "NoScripts", "NoScripts.dll"),
]


def make_fake_game(root):
    game = os.path.join(root, "game")
    for relative in FAKE_FILES:
        path = os.path.join(game, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(f"// {relative}\n")
    return game


def build_quietly(game, sandbox):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        make_sandbox.build(game, sandbox)
    return output.getvalue()


def sandbox_files(sandbox):
    folder = os.path.join(sandbox, PLUGINS)
    if not os.path.isdir(folder):
        return []
    return sorted(os.path.relpath(os.path.join(root, name), folder).replace(os.sep, "/")
                  for root, _dirs, names in os.walk(folder) for name in names)


def test_mirrors_plugin_scripts(root):
    game = make_fake_game(root)
    sandbox = os.path.join(root, "sandbox")
    output = build_quietly(game, sandbox)
    files = sandbox_files(sandbox)
    warned = "CP2077Coop" in output and "has .reds files" in output
    print(f"  sandbox plugin files {files}; own-plugin warning {warned}")
    return files == ["CP2077CoopNet/Scripts/Helpers.reds", "CP2077CoopNet/Scripts/sub/Nested.reds",
                     "Codeware/Scripts/Codeware.reds"] and warned


def test_removed_plugin_dropped(root):
    game = make_fake_game(root)
    sandbox = os.path.join(root, "sandbox")
    build_quietly(game, sandbox)
    shutil.rmtree(os.path.join(game, PLUGINS, "CP2077CoopNet"))
    os.remove(os.path.join(game, PLUGINS, "Codeware", "Scripts", "Codeware.reds"))
    output = build_quietly(game, sandbox)
    files = sandbox_files(sandbox)
    print(f"  after removing CoopNet and Codeware's script: {files}")
    return files == [] and "plugin scripts: Codeware" in output


def test_refuses_overlap(root):
    game = make_fake_game(root)
    refused = []
    for sandbox in (os.path.join(game, "sandbox"), os.path.join(make_sandbox.REPO, "sandbox_should_not_exist")):
        try:
            build_quietly(game, sandbox)
            refused.append(False)
        except make_sandbox.SetupError as error:
            print(f"  refused: {error}")
            refused.append(True)
    return refused == [True, True] and not os.path.exists(os.path.join(make_sandbox.REPO, "sandbox_should_not_exist"))


def main():
    tests = {
        "S1 every plugin's Scripts folder mirrored (subfolders too), not our own, stray own .reds reported": test_mirrors_plugin_scripts,
        "S2 a plugin removed from the game is removed from the sandbox": test_removed_plugin_dropped,
        "S3 sandbox inside the game folder or the repo refused": test_refuses_overlap,
    }
    results = {}
    for name, test in tests.items():
        print(f"-- {name}")
        root = tempfile.mkdtemp(prefix="coopsandbox_")
        try:
            results[name] = bool(test(root))
        except Exception as error:  # report and keep going
            print(f"  EXCEPTION {error!r}")
            results[name] = False
        finally:
            shutil.rmtree(root, ignore_errors=True)
    for name, passed in results.items():
        print(f"{'PASS' if passed else 'FAIL'}  {name}")
    return all(results.values())


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
