"""CP2077 Coop developer kit: fast local test setup.

Commands:
    make-instance SRC DEST     second game folder; big archives are hardlinked (no extra disk)
    deploy GAME [GAME ...]     copy the mod files from this package into game folders
    server GAME local|warsaw|IP:PORT [...]
    role GAME host|joiner
    status GAME [GAME ...]     show server, role and mod version of each folder

Examples:
    python coop-tools/devkit.py make-instance "G:/.../Cyberpunk 2077 - Baseline" "G:/.../Cyberpunk 2077 - Test B"
    python coop-tools/devkit.py deploy "G:/.../Cyberpunk 2077 - Baseline" "G:/.../Cyberpunk 2077 - Test B"
    python coop-tools/devkit.py server "G:/.../Cyberpunk 2077 - Baseline" local
"""
import argparse
import os
import re
import shutil
import sys

PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOD_DIR = os.path.join("bin", "x64", "plugins", "cyber_engine_tweaks", "mods", "CP2077Coop")
SERVER_INI = os.path.join("red4ext", "plugins", "CP2077Coop", "server.ini")
WARSAW = ("51.68.153.130", "11778")
LOCAL = ("127.0.0.1", "11778")

# Read-only game data: hardlinked so the second instance costs no disk space.
HARDLINK_DIRS = [os.path.join("archive", "pc", "content"), os.path.join("archive", "pc", "ep1")]
HARDLINK_EXTENSIONS = {".cache"}  # engine/shader caches

# Never copied into a new instance: per-instance runtime output.
SKIP_NAMES = {"role.txt", "monitor_status.txt", "coop_monitor_history.csv", "coop_relay.log"}
SKIP_DIRS = {os.path.join("r6", "logs"), os.path.join("red4ext", "logs")}

# Files deployed from this package into a game folder.
DEPLOY_FILES = [
    os.path.join(MOD_DIR, "init.lua"),
    os.path.join("r6", "scripts", "CP2077Coop", "natives.reds"),
    os.path.join("r6", "scripts", "CP2077Coop", "remote.reds"),
    os.path.join("r6", "scripts", "CP2077Coop", "state.reds"),
    os.path.join("red4ext", "plugins", "CP2077Coop", "CP2077Coop.dll"),
]
DEPLOY_DIRS = ["coop-tools", os.path.join("red4ext", "plugins", "Codeware")]
OPTIONAL_DEPLOY_FILES = [os.path.join("r6", "scripts", "CP2077Coop", "autoload.reds")]


def require_game(path):
    if not os.path.isfile(os.path.join(path, "bin", "x64", "Cyberpunk2077.exe")):
        sys.exit(f"not a game folder (no bin/x64/Cyberpunk2077.exe): {path}")


def is_hardlink_candidate(relative):
    if any(relative.startswith(d + os.sep) for d in HARDLINK_DIRS):
        return True
    return relative.startswith("engine" + os.sep) and os.path.splitext(relative)[1] in HARDLINK_EXTENSIONS


def make_instance(source, dest):
    require_game(source)
    if os.path.exists(dest):
        sys.exit(f"destination already exists: {dest}")
    if os.path.splitdrive(os.path.abspath(source))[0].lower() != os.path.splitdrive(os.path.abspath(dest))[0].lower():
        sys.exit("source and destination must be on the same drive for hardlinks")

    linked = copied = copied_bytes = 0
    for root, dirs, files in os.walk(source):
        relative_root = os.path.relpath(root, source)
        dirs[:] = [d for d in dirs if os.path.normpath(os.path.join(relative_root, d)) not in SKIP_DIRS]
        os.makedirs(os.path.join(dest, relative_root), exist_ok=True)
        for name in files:
            if name in SKIP_NAMES or name.endswith(".log"):
                continue
            relative = os.path.normpath(os.path.join(relative_root, name))
            src_file = os.path.join(source, relative)
            dst_file = os.path.join(dest, relative)
            if is_hardlink_candidate(relative):
                os.link(src_file, dst_file)
                linked += 1
            else:
                shutil.copy2(src_file, dst_file)
                copied += 1
                copied_bytes += os.path.getsize(src_file)
    print(f"created {dest}: {linked} files hardlinked, {copied} copied ({copied_bytes / 1e9:.2f} GB)")


def deploy(games):
    for game in games:
        require_game(game)
        for relative in DEPLOY_FILES + [f for f in OPTIONAL_DEPLOY_FILES if os.path.exists(os.path.join(PACKAGE_ROOT, f))]:
            target = os.path.join(game, relative)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(os.path.join(PACKAGE_ROOT, relative), target)
        for relative in OPTIONAL_DEPLOY_FILES:
            target = os.path.join(game, relative)
            if not os.path.exists(os.path.join(PACKAGE_ROOT, relative)) and os.path.exists(target):
                os.replace(target, target + ".disabled")
        for relative in DEPLOY_DIRS:
            shutil.copytree(os.path.join(PACKAGE_ROOT, relative), os.path.join(game, relative), dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns(*SKIP_NAMES, "__pycache__", "*.log"))
        print(f"deployed {mod_version(PACKAGE_ROOT)} -> {game}")


def read_server(game):
    values = {}
    try:
        with open(os.path.join(game, SERVER_INI), encoding="utf-8") as handle:
            for line in handle:
                if "=" in line:
                    key, value = line.strip().split("=", 1)
                    values[key] = value
    except OSError:
        return None
    return values.get("server_ip"), values.get("server_port")


def set_server(game, target):
    require_game(game)
    if target == "local":
        ip, port = LOCAL
    elif target == "warsaw":
        ip, port = WARSAW
    else:
        match = re.fullmatch(r"([\d.]+):(\d+)", target)
        if not match:
            sys.exit("server must be local, warsaw or IP:PORT")
        ip, port = match.groups()
    with open(os.path.join(game, SERVER_INI), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(f"server_ip={ip}\nserver_port={port}\n")
    print(f"{game}: server -> {ip}:{port}")


def set_role(game, role):
    require_game(game)
    with open(os.path.join(game, MOD_DIR, "role.txt"), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(role + "\n")
    print(f"{game}: role -> {role}")


def mod_version(game):
    try:
        with open(os.path.join(game, MOD_DIR, "init.lua"), encoding="utf-8") as handle:
            match = re.search(r'VERSION = "([^"]+)"', handle.read())
        return f"v{match.group(1)}" if match else "unknown"
    except OSError:
        return "not installed"


def status(games):
    for game in games:
        server = read_server(game)
        try:
            with open(os.path.join(game, MOD_DIR, "role.txt"), encoding="utf-8") as handle:
                role = handle.read().strip()
        except OSError:
            role = "(init.lua default)"
        autoload = os.path.exists(os.path.join(game, "r6", "scripts", "CP2077Coop", "autoload.reds"))
        print(f"{os.path.basename(game):<34} mod={mod_version(game):<10} server={':'.join(server) if server else '?':<22} role={role:<8} autoload={'on' if autoload else 'off'}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("make-instance"); p.add_argument("source"); p.add_argument("dest")
    p = sub.add_parser("deploy"); p.add_argument("games", nargs="+")
    p = sub.add_parser("server"); p.add_argument("game"); p.add_argument("target")
    p = sub.add_parser("role"); p.add_argument("game"); p.add_argument("role", choices=["host", "joiner"])
    p = sub.add_parser("status"); p.add_argument("games", nargs="+")
    args = parser.parse_args()

    if args.command == "make-instance":
        make_instance(args.source, args.dest)
    elif args.command == "deploy":
        deploy(args.games)
    elif args.command == "server":
        set_server(args.game, args.target)
    elif args.command == "role":
        set_role(args.game, args.role)
    elif args.command == "status":
        status(args.games)


if __name__ == "__main__":
    main()
