"""Static checks of the built plugin with pefile (no game, nothing executed).

Checks: x64 image, DLL flag, the three RED4ext exports with undecorated names, what Supports()
returns (decoded from its machine code), no dependency on the VC++ redistributable (static CRT),
that every Net_* native name is present in the image, and that the Net_Version() string in the
image carries the version from CMakeLists.txt and the wire protocol from src/core/Version.hpp.

The native list is read from src/core/LoadReport.hpp (kNativeNames), and the same names must be
registered in src/plugin/Main.cpp and declared in scripts/CP2077CoopNet/Natives.reds, so the C++
list, the redscript declarations and the DLL cannot drift apart.

Usage:
    python tools/verify_exports.py build/Release/CP2077CoopNet.dll
"""
import os
import re
import sys

import pefile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUIRED_EXPORTS = {"Main", "Query", "Supports"}
REDIST_DLLS = {"msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll"}
REGISTERED_MARKER = b"registered Net_* natives"


def read_text(relative_path):
    with open(os.path.join(ROOT, relative_path), encoding="utf-8") as handle:
        return handle.read()


def native_names():
    """kNativeNames from src/core/LoadReport.hpp, in order."""
    header = read_text(os.path.join("src", "core", "LoadReport.hpp"))
    match = re.search(r"kNativeNames\s*=\s*\{(.*?)\};", header, re.S)
    if match is None:
        raise SystemExit("kNativeNames not found in src/core/LoadReport.hpp")
    return re.findall(r'"(Net_\w+)"', match.group(1))


def cmake_version():
    """The semver CMakeLists.txt builds: project VERSION plus the COOPNET_PRERELEASE_* label."""
    text = read_text("CMakeLists.txt")
    match = re.search(r"project\(CP2077CoopNet\s+VERSION\s+(\d+\.\d+\.\d+)", text)
    kind = re.search(r'set\(COOPNET_PRERELEASE_TYPE\s+"([a-z]*)"\)', text)
    number = re.search(r"set\(COOPNET_PRERELEASE_NUMBER\s+(\d+)\)", text)
    if match is None or kind is None or number is None:
        raise SystemExit("project(CP2077CoopNet VERSION x.y.z) or COOPNET_PRERELEASE_* not found in CMakeLists.txt")
    return match.group(1) + (f"-{kind.group(1)}.{number.group(1)}" if kind.group(1) else "")


def wire_protocol():
    """COOPNET_WIRE_MAJOR.COOPNET_WIRE_MINOR from src/core/Version.hpp, e.g. "2.1"."""
    header = read_text(os.path.join("src", "core", "Version.hpp"))
    major = re.search(r"#define COOPNET_WIRE_MAJOR (\d+)", header)
    minor = re.search(r"#define COOPNET_WIRE_MINOR (\d+)", header)
    if major is None or minor is None:
        raise SystemExit("COOPNET_WIRE_MAJOR/MINOR not found in src/core/Version.hpp")
    return f"{major.group(1)}.{minor.group(1)}"


def source_consistency(names):
    """Every native is registered in Main.cpp and declared in Natives.reds, and nothing extra is declared."""
    failures = []
    main_cpp = read_text(os.path.join("src", "plugin", "Main.cpp"))
    reds = read_text(os.path.join("scripts", "CP2077CoopNet", "Natives.reds"))
    registered = re.findall(r'RegisterGlobal<[^>]+>\(rtti,\s*report,\s*"(Net_\w+)"', main_cpp)
    declared = re.findall(r"public static native func (Net_\w+)\(", reds)
    print(f"natives: LoadReport.hpp={len(names)}, Main.cpp registers={len(registered)}, Natives.reds declares={len(declared)}")
    if registered != names:
        failures.append(f"Main.cpp registers {registered}, LoadReport.hpp lists {names}")
    if sorted(declared) != sorted(names):
        failures.append(f"Natives.reds declares {sorted(declared)}, LoadReport.hpp lists {sorted(names)}")
    return failures


def decode_constant_return(code):
    """Recognises `mov eax, imm32; ret` and `xor eax, eax; ret`."""
    if len(code) >= 6 and code[0] == 0xB8 and code[5] == 0xC3:
        return int.from_bytes(code[1:5], "little")
    if code[:3] == b"\x33\xc0\xc3" or code[:3] == b"\x31\xc0\xc3":
        return 0
    return None


def main(path):
    pe = pefile.PE(path)
    failures = []

    machine_ok = pe.FILE_HEADER.Machine == pefile.MACHINE_TYPE["IMAGE_FILE_MACHINE_AMD64"]
    is_dll = bool(pe.FILE_HEADER.Characteristics & pefile.IMAGE_CHARACTERISTICS["IMAGE_FILE_DLL"])
    print(f"file: {path}")
    print(f"machine AMD64: {machine_ok}, DLL: {is_dll}, image size: {pe.OPTIONAL_HEADER.SizeOfImage} bytes")
    if not machine_ok or not is_dll:
        failures.append("not an x64 DLL")

    exports = {}
    for symbol in pe.DIRECTORY_ENTRY_EXPORT.symbols:
        name = symbol.name.decode() if symbol.name else f"#{symbol.ordinal}"
        exports[name] = symbol.address
    print("exports:")
    for name, rva in sorted(exports.items()):
        print(f"  {name:<10} rva=0x{rva:08x} first bytes={pe.get_data(rva, 12).hex()}")
    missing = REQUIRED_EXPORTS - exports.keys()
    if missing:
        failures.append(f"missing exports: {sorted(missing)}")

    if "Supports" in exports:
        api_version = decode_constant_return(pe.get_data(exports["Supports"], 8))
        print(f"Supports() returns: {api_version} (RED4EXT_API_VERSION_1 = 1)")
        if api_version != 1:
            failures.append(f"Supports() returns {api_version}, expected 1")

    imports = sorted(entry.dll.decode().lower() for entry in pe.DIRECTORY_ENTRY_IMPORT)
    print(f"imports: {', '.join(imports)}")
    redist = REDIST_DLLS.intersection(imports)
    if redist:
        failures.append(f"depends on the VC++ redistributable: {sorted(redist)}")
    if "ws2_32.dll" not in imports:
        failures.append("WS2_32.dll not imported")

    names = native_names()
    failures.extend(source_consistency(names))
    image = pe.get_memory_mapped_image()
    for native in names:
        if (native.encode() + b"\x00") not in image:
            failures.append(f"native name {native} not found in image")
    print(f"native names present ({len(names)}): {all((n.encode() + b'\x00') in image for n in names)}")

    expected_version = cmake_version()
    expected_wire = wire_protocol()
    found = re.findall(rb"CP2077CoopNet (\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?) proto (\d+\.\d+)\x00", image)
    shown = [f"CP2077CoopNet {semver.decode()} proto {proto.decode()}" for semver, proto in found]
    print(f"Net_Version strings in image: {shown}"
          f" (CMakeLists.txt VERSION {expected_version})")
    if not found:
        failures.append("no 'CP2077CoopNet <semver> proto <major>.<minor>' string in the image")
    elif any(version.decode() != expected_version for version, _ in found):
        failures.append(f"Net_Version string does not match CMakeLists.txt VERSION {expected_version}")
    elif any(proto.decode() != expected_wire for _, proto in found):
        failures.append(f"Net_Version string does not name wire protocol {expected_wire} (src/core/Version.hpp)")

    # The Phase 1 log grep must only ever match the summary line (kRegisteredMarker), never another string.
    marker_count = image.count(REGISTERED_MARKER)
    print(f"'{REGISTERED_MARKER.decode()}' occurrences in image: {marker_count} (expected 1)")
    if marker_count != 1:
        failures.append(f"'{REGISTERED_MARKER.decode()}' appears {marker_count} times in the image, expected 1")

    if failures:
        print("VERIFY FAIL:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("VERIFY PASS")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
