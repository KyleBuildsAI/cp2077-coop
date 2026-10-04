"""Compile-check a game folder's redscript mods WITHOUT launching the game or showing popups.

Uses the redscript compiler library (engine/tools/scc_lib.dll) directly with its
error popup disabled, compiles r6/scripts plus RED4ext plugin Scripts folders
(e.g. Codeware) against the vanilla r6/cache/final.redscripts, and writes the
result into a fresh temporary folder (deleted afterwards) so the game's own cache
is never touched and concurrent checks never share an output file.

Usage:
    python coop-tools/scc_check.py "G:/SteamLibrary/steamapps/common/Cyberpunk 2077 - Baseline"
    python coop-tools/scc_check.py GAME --scripts extra/folder     # add more script folders

Exit code 0 = compiled, 1 = compile errors, 2 = setup problem.
"""
import argparse
import ctypes
import os
import shutil
import sys
import tempfile

ERROR_BUFFER_BYTES = 1 << 20


def load_compiler(game_dir):
    library_path = os.path.join(game_dir, "engine", "tools", "scc_lib.dll")
    if not os.path.isfile(library_path):
        sys.exit(f"redscript compiler not found: {library_path}")
    library = ctypes.CDLL(library_path)
    library.scc_settings_new.restype = ctypes.c_void_p
    library.scc_settings_new.argtypes = [ctypes.c_char_p]
    for name in ("scc_settings_set_custom_cache_file", "scc_settings_set_output_cache_file", "scc_settings_add_script_path"):
        getattr(library, name).argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        getattr(library, name).restype = None
    library.scc_settings_disable_error_popup.argtypes = [ctypes.c_void_p]
    library.scc_settings_disable_error_popup.restype = None
    library.scc_compile.argtypes = [ctypes.c_void_p]
    library.scc_compile.restype = ctypes.c_void_p
    library.scc_get_success.argtypes = [ctypes.c_void_p]
    library.scc_get_success.restype = ctypes.c_int
    library.scc_copy_error.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
    library.scc_copy_error.restype = ctypes.c_size_t
    library.scc_free_result.argtypes = [ctypes.c_void_p]
    library.scc_free_result.restype = None
    return library


def plugin_script_dirs(game_dir):
    plugins = os.path.join(game_dir, "red4ext", "plugins")
    found = []
    if os.path.isdir(plugins):
        for name in sorted(os.listdir(plugins)):
            scripts = os.path.join(plugins, name, "Scripts")
            if os.path.isdir(scripts):
                found.append(scripts)
    return found


def compile_check(game_dir, extra_dirs):
    library = load_compiler(game_dir)
    r6_dir = os.path.join(game_dir, "r6")
    vanilla_cache = os.path.join(r6_dir, "cache", "final.redscripts")
    if not os.path.isfile(vanilla_cache):
        print(f"vanilla script cache missing: {vanilla_cache}")
        return 2

    script_dirs = [os.path.join(r6_dir, "scripts")] + plugin_script_dirs(game_dir) + list(extra_dirs)
    # a folder per call: two checks at once must not share (or delete) each other's output
    out_dir = tempfile.mkdtemp(prefix="cp2077coop_scc_")
    try:
        output = os.path.join(out_dir, "check.redscripts")
        return compile_into(library, game_dir, r6_dir, vanilla_cache, script_dirs, output)
    finally:
        try:
            shutil.rmtree(out_dir)
        except OSError as error:  # a leftover temp folder must not turn a good compile into a failure
            print(f"WARN: could not remove {out_dir}: {error}")


def compile_into(library, game_dir, r6_dir, vanilla_cache, script_dirs, output):
    settings = library.scc_settings_new(r6_dir.encode("utf-8"))
    library.scc_settings_disable_error_popup(settings)
    library.scc_settings_set_custom_cache_file(settings, vanilla_cache.encode("utf-8"))
    library.scc_settings_set_output_cache_file(settings, output.encode("utf-8"))
    for directory in script_dirs[1:]:
        library.scc_settings_add_script_path(settings, directory.encode("utf-8"))

    result = library.scc_compile(settings)
    try:
        success = library.scc_get_success(result) != 0
        if success:
            print(f"OK: compiled {', '.join(os.path.relpath(d, game_dir) for d in script_dirs)}")
            return 0
        buffer = ctypes.create_string_buffer(ERROR_BUFFER_BYTES)
        library.scc_copy_error(result, buffer, ERROR_BUFFER_BYTES)
        print("COMPILE FAILED:")
        print(buffer.value.decode("utf-8", errors="replace"))
        return 1
    finally:
        library.scc_free_result(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("game", help="game folder (contains engine, r6, red4ext)")
    parser.add_argument("--scripts", action="append", default=[], help="extra script folder (repeatable)")
    args = parser.parse_args()
    sys.exit(compile_check(os.path.abspath(args.game), args.scripts))


if __name__ == "__main__":
    main()
