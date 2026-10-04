# Configures and builds CP2077CoopNet with the VS 2022 Community toolchain, runs the offline
# checks and stages an install folder under dist\ (never touches a game directory).
#
#   powershell -ExecutionPolicy Bypass -File tools\build.ps1                  # build + unit tests + export checks
#   powershell -ExecutionPolicy Bypass -File tools\build.ps1 -Loopback        # also the relay integration test
#   powershell -ExecutionPolicy Bypass -File tools\build.ps1 -Clean -All      # fresh build dir, every offline test
#
# Every run also checks the v2 codec: coopnet_v2_tests, the 20,000-input fuzzer (plain and, when
# the MSVC AddressSanitizer runtime is installed, under /fsanitize=address) and the golden vectors
# against the relay's proto.py in both directions (tools\v2_golden.py). The relay checkout is
# ..\relay or $env:COOPNET_RELAY_DIR.
# -All implies -Loopback and also runs tests\test_relay_protocol.py, tests\test_lua_helper.py, the
# relay's unit tests and its tools\check_c_header.py.
# The Lua test needs the lupa package: pip install lupa, or set PYTHONPATH to a folder that has it.
param(
    [switch]$Loopback,
    [switch]$All,
    [switch]$Clean,
    [string]$VsInstance = 'C:/Program Files/Microsoft Visual Studio/2022/Community'
)
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$build = Join-Path $root 'build'
$release = Join-Path $build 'Release'
$relay = if ($env:COOPNET_RELAY_DIR) { $env:COOPNET_RELAY_DIR } else { Join-Path (Split-Path -Parent $root) 'relay' }
if (-not (Test-Path (Join-Path $relay 'coopnet\proto.py'))) {
    throw "relay checkout not found at $relay (set COOPNET_RELAY_DIR); the v2 golden vectors need its proto.py"
}

function Invoke-Step([string]$Name, [scriptblock]$Command) {
    Write-Host "==> $Name"
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$Name failed (exit $LASTEXITCODE)" }
}

if ($Clean -and (Test-Path $build)) {
    Write-Host "==> removing $build (fresh build dir)"
    Remove-Item -Recurse -Force $build
}

if (-not (Test-Path (Join-Path $root 'deps\RED4ext.SDK\CMakeLists.txt'))) {
    Invoke-Step 'fetch RED4ext.SDK' { & powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'fetch_deps.ps1') }
}

Invoke-Step 'configure' { cmake -S $root -B $build -G 'Visual Studio 17 2022' -A x64 "-DCMAKE_GENERATOR_INSTANCE=$VsInstance" }
Invoke-Step 'build' { cmake --build $build --config Release --parallel }
Invoke-Step 'unit tests' { & (Join-Path $release 'coopnet_tests.exe') }
Invoke-Step 'export check (pefile)' { python (Join-Path $PSScriptRoot 'verify_exports.py') (Join-Path $release 'CP2077CoopNet.dll') }
Invoke-Step 'LoadLibrary probe' { & (Join-Path $release 'coopnet_plugin_probe.exe') (Join-Path $release 'CP2077CoopNet.dll') }
Invoke-Step 'v2 codec unit tests' { & (Join-Path $release 'coopnet_v2_tests.exe') }
Invoke-Step 'v2 codec fuzz (20,000 inputs)' { & (Join-Path $release 'coopnet_v2_fuzz.exe') 20000 }
$asanFuzz = Join-Path $release 'coopnet_v2_fuzz_asan.exe'
if (Test-Path $asanFuzz) {
    Invoke-Step 'v2 codec fuzz under AddressSanitizer (20,000 inputs)' { & $asanFuzz 20000 }
} else {
    Write-Host '==> v2 codec fuzz under AddressSanitizer skipped: no MSVC ASan runtime'
}
Invoke-Step 'v2 golden vectors against proto.py (both directions)' {
    python (Join-Path $PSScriptRoot 'v2_golden.py') --relay $relay run --exe (Join-Path $release 'coopnet_v2_golden.exe')
}
if ($Loopback -or $All) {
    Invoke-Step 'relay loopback' { python (Join-Path $PSScriptRoot 'run_loopback.py') }
}
if ($All) {
    Invoke-Step 'relay protocol test' { python (Join-Path $root 'tests\test_relay_protocol.py') }
    Invoke-Step 'Lua helper test (LuaJIT 2.1)' { python (Join-Path $root 'tests\test_lua_helper.py') }
    Invoke-Step 'relay unit tests' { Push-Location $relay; try { python -m unittest discover -s tests } finally { Pop-Location } }
    Invoke-Step 'relay C header check (MSVC)' { python (Join-Path $relay 'tools\check_c_header.py') }
}

# Install layout: <game>\red4ext\plugins\CP2077CoopNet\{CP2077CoopNet.dll, Scripts\*.reds}
# The stage folder is rebuilt from scratch so a removed .reds file cannot linger in dist\.
$stage = Join-Path $root 'dist\red4ext\plugins\CP2077CoopNet'
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Force (Join-Path $stage 'Scripts') | Out-Null
Copy-Item (Join-Path $release 'CP2077CoopNet.dll') $stage -Force
Copy-Item (Join-Path $root 'scripts\CP2077CoopNet\*.reds') (Join-Path $stage 'Scripts') -Force
Write-Host "staged $stage"
Get-ChildItem -Recurse -File $stage | ForEach-Object {
    $hash = (Get-FileHash -Algorithm SHA256 $_.FullName).Hash
    Write-Host ("  {0,-40} {1,9} bytes  SHA256 {2}" -f $_.FullName.Substring($stage.Length + 1), $_.Length, $hash)
}
