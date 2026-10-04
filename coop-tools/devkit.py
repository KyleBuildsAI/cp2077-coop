"""CP2077 Coop developer kit: fast local test setup.

Commands:
    make-instance SRC DEST     second game folder; big archives are hardlinked (no extra disk)
    deploy GAME [GAME ...]     copy the mod files from this package into game folders
    server GAME local|warsaw|IP:PORT [...]
    role GAME host|joiner
    status GAME [GAME ...]     show server, role and mod version of each folder
    modlist GAME [GAME ...]    write the installed-mods list the coop panel compares

Examples:
    python coop-tools/devkit.py make-instance "G:/.../Cyberpunk 2077 - Baseline" "G:/.../Cyberpunk 2077 - Test B"
    python coop-tools/devkit.py deploy "G:/.../Cyberpunk 2077 - Baseline" "G:/.../Cyberpunk 2077 - Test B"
    python coop-tools/devkit.py server "G:/.../Cyberpunk 2077 - Baseline" local
"""
import argparse
import filecmp
import fnmatch
import glob
import os
import re
import shutil
import sys
import time

PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOD_DIR = os.path.join("bin", "x64", "plugins", "cyber_engine_tweaks", "mods", "CP2077Coop")
SERVER_INI = os.path.join("red4ext", "plugins", "CP2077Coop", "server.ini")
WARSAW = ("51.68.153.130", "11778")
LOCAL = ("127.0.0.1", "11778")

# Read-only game data: hardlinked so the second instance costs no disk space.
HARDLINK_DIRS = [os.path.join("archive", "pc", "content"), os.path.join("archive", "pc", "ep1")]
HARDLINK_EXTENSIONS = {".cache"}  # engine/shader caches

# Never copied into a new instance or deployed: per-instance runtime output.
SKIP_NAMES = {"role.txt", "transport.ini", "monitor_status.txt", "coop_monitor_history.csv", "coop_relay.log",
              "coop_stats_host.txt", "coop_stats_joiner.txt", "coop_events.log"}
# The same by pattern: history files the monitor rotated out, its interrupted status swap, logs.
SKIP_PATTERNS = ("coop_monitor_history*.csv", "monitor_status.txt.tmp", "*.log")
# Bot autostart switch (init.lua Bot.FILE): belongs only in the test host's mod folder. A cloned
# instance is usually the joiner, and the bot would replace its player's real movement.
HOST_ONLY_NAMES = {"testpattern.txt"}
SKIP_DIRS = {os.path.join("r6", "logs"), os.path.join("red4ext", "logs")}

SCRIPTS_DIR = os.path.join("r6", "scripts", "CP2077Coop")

# Files deployed from this package into a game folder (plus every *.reds in SCRIPTS_DIR).
DEPLOY_FILES = [
    os.path.join(MOD_DIR, "init.lua"),
    os.path.join(MOD_DIR, "net_transport.lua"),
    os.path.join(MOD_DIR, "testnpc.lua"),
    os.path.join(MOD_DIR, "npc_test.lua"),
    os.path.join("red4ext", "plugins", "CP2077Coop", "CP2077Coop.dll"),
]
DEPLOY_DIRS = ["coop-tools", os.path.join("red4ext", "plugins", "Codeware")]


def require_game(path):
    if not os.path.isfile(os.path.join(path, "bin", "x64", "Cyberpunk2077.exe")):
        sys.exit(f"not a game folder (no bin/x64/Cyberpunk2077.exe): {path}")


def is_skipped(name):
    """True for runtime output that make-instance and deploy never copy."""
    return name in SKIP_NAMES or any(fnmatch.fnmatch(name, pattern) for pattern in SKIP_PATTERNS)


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
    skipped_bot = False
    for root, dirs, files in os.walk(source):
        relative_root = os.path.relpath(root, source)
        dirs[:] = [d for d in dirs if os.path.normpath(os.path.join(relative_root, d)) not in SKIP_DIRS]
        os.makedirs(os.path.join(dest, relative_root), exist_ok=True)
        for name in files:
            if is_skipped(name):
                continue
            if name in HOST_ONLY_NAMES:
                skipped_bot = True
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
    if skipped_bot:
        print("  testpattern.txt not copied: the new instance does not auto-start the test bot "
              "(create it only in the host's mod folder)")


def package_scripts():
    return sorted(os.path.join(SCRIPTS_DIR, os.path.basename(p)) for p in glob.glob(os.path.join(PACKAGE_ROOT, SCRIPTS_DIR, "*.reds")))


def backup_if_changed(game, relative, stamp):
    """Keep a copy of any file we are about to overwrite with different content."""
    target = os.path.join(game, relative)
    source = os.path.join(PACKAGE_ROOT, relative)
    if os.path.exists(target) and not filecmp.cmp(source, target, shallow=False):
        backup = os.path.join(game, "coop-backup", stamp, relative)
        os.makedirs(os.path.dirname(backup), exist_ok=True)
        shutil.copy2(target, backup)
        return True
    return False


def deploy(games):
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for game in games:
        require_game(game)
        backed_up = 0
        for relative in DEPLOY_FILES + package_scripts():
            target = os.path.join(game, relative)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            backed_up += backup_if_changed(game, relative, stamp)
            shutil.copy2(os.path.join(PACKAGE_ROOT, relative), target)
        shipped = {os.path.basename(p) for p in package_scripts()}
        for extra in sorted(glob.glob(os.path.join(game, SCRIPTS_DIR, "*.reds"))):
            if os.path.basename(extra) not in shipped:
                print(f"  WARNING: {os.path.relpath(extra, game)} is not part of this package (left in place)")
        if backed_up:
            print(f"  backed up {backed_up} overwritten file(s) to {os.path.join(game, 'coop-backup', stamp)}")
        for relative in DEPLOY_DIRS:
            shutil.copytree(os.path.join(PACKAGE_ROOT, relative), os.path.join(game, relative), dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns(*SKIP_NAMES, *SKIP_PATTERNS, "__pycache__"))
        write_modlist(game)
        print(f"deployed {mod_version(PACKAGE_ROOT)} -> {game}")


# Infrastructure shared by every coop install; not shown in the mod comparison.
MODLIST_IGNORE = {"cp2077coop", "codeware", "cyber_engine_tweaks", "red4ext"}


def collect_mods(game):
    """Installed mods as 'category/name', sorted. Categories match common install locations."""
    sources = [
        ("cet", os.path.join("bin", "x64", "plugins", "cyber_engine_tweaks", "mods"), "dirs"),
        ("redscript", os.path.join("r6", "scripts"), "entries"),
        ("red4ext", os.path.join("red4ext", "plugins"), "dirs"),
        ("archive", os.path.join("archive", "pc", "mod"), "files"),
        ("tweak", os.path.join("r6", "tweaks"), "entries"),
        ("redmod", "mods", "dirs"),
    ]
    found = set()
    for category, relative, kind in sources:
        folder = os.path.join(game, relative)
        if not os.path.isdir(folder):
            continue
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            if name.startswith(".") or name.lower() in MODLIST_IGNORE:
                continue
            if kind == "dirs" and not os.path.isdir(path):
                continue
            if kind == "files" and not os.path.isfile(path):
                continue
            if kind == "files" and not name.lower().endswith((".archive", ".xl")):
                continue
            found.add(f"{category}/{os.path.splitext(name)[0] if kind == 'files' else name}")
    return sorted(found)


def write_modlist(game):
    mods = collect_mods(game)
    target = os.path.join(game, MOD_DIR, "modlist.txt")
    with open(target, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("".join(m + "\n" for m in mods))
    print(f"{game}: {len(mods)} mods listed in modlist.txt")


def read_server(game):
    """(ip, port) from server.ini (either may be None), or None when the file is unreadable.

    utf-8-sig: a BOM (PowerShell 5 Set-Content -Encoding UTF8, old Notepad) would
    otherwise glue itself to the first key and hide server_ip.
    """
    values = {}
    try:
        with open(os.path.join(game, SERVER_INI), encoding="utf-8-sig", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if line.startswith((";", "#")) or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    except OSError as error:
        print(f"  cannot read {SERVER_INI} in {game}: {error}")
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
        print(f"{os.path.basename(game):<34} mod={mod_version(game):<10} server={':'.join(server) if server and all(server) else '?':<22} role={role:<8} autoload={'on' if autoload else 'off'}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("make-instance"); p.add_argument("source"); p.add_argument("dest")
    p = sub.add_parser("deploy"); p.add_argument("games", nargs="+")
    p = sub.add_parser("server"); p.add_argument("game"); p.add_argument("target")
    p = sub.add_parser("role"); p.add_argument("game"); p.add_argument("role", choices=["host", "joiner"])
    p = sub.add_parser("status"); p.add_argument("games", nargs="+")
    p = sub.add_parser("modlist"); p.add_argument("games", nargs="+")
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
    elif args.command == "modlist":
        for game in args.games:
            write_modlist(game)


if __name__ == "__main__":
    main()
