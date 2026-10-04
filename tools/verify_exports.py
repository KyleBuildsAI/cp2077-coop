"""Static checks of the built plugin with pefile (no game, nothing executed).

Checks: x64 image, DLL flag, the three RED4ext exports with undecorated names, what Supports()
returns (decoded from its machine code), no dependency on the VC++ redistributable (static CRT),
and that every Net_* native name is present in the image.

Usage:
    python tools/verify_exports.py build/Release/CP2077CoopNet.dll
"""
import sys

import pefile

REQUIRED_EXPORTS = {"Main", "Query", "Supports"}
NATIVE_NAMES = [
    "Net_Connect", "Net_ConnectRoom", "Net_Disconnect", "Net_Send", "Net_SendTo", "Net_Poll", "Net_Stats",
    "Net_LocalId",
]
REDIST_DLLS = {"msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll"}


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

    image = pe.get_memory_mapped_image()
    for native in NATIVE_NAMES:
        if (native.encode() + b"\x00") not in image:
            failures.append(f"native name {native} not found in image")
    print(f"native names present: {all((n.encode() + b'\x00') in image for n in NATIVE_NAMES)}")

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
