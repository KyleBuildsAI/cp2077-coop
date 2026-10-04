# Configures and builds CP2077CoopNet with the VS 2022 Community toolchain, runs the offline
# checks and stages an install folder under dist\ (never touches a game directory).
#
#   powershell -ExecutionPolicy Bypass -File tools\build.ps1            # build + unit tests + export checks
#   powershell -ExecutionPolicy Bypass -File tools\build.ps1 -Loopback  # also the relay integration test
param(
    [switch]$Loopback,
    [string]$VsInstance = 'C:/Program Files/Microsoft Visual Studio/2022/Community'
)
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$build = Join-Path $root 'build'
$release = Join-Path $build 'Release'

function Invoke-Step([string]$Name, [scriptblock]$Command) {
    Write-Host "==> $Name"
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$Name failed (exit $LASTEXITCODE)" }
}

if (-not (Test-Path (Join-Path $root 'deps\RED4ext.SDK\CMakeLists.txt'))) {
    Invoke-Step 'fetch RED4ext.SDK' { & powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'fetch_deps.ps1') }
}

Invoke-Step 'configure' { cmake -S $root -B $build -G 'Visual Studio 17 2022' -A x64 "-DCMAKE_GENERATOR_INSTANCE=$VsInstance" }
Invoke-Step 'build' { cmake --build $build --config Release --parallel }
Invoke-Step 'unit tests' { & (Join-Path $release 'coopnet_tests.exe') }
Invoke-Step 'export check (pefile)' { python (Join-Path $PSScriptRoot 'verify_exports.py') (Join-Path $release 'CP2077CoopNet.dll') }
Invoke-Step 'LoadLibrary probe' { & (Join-Path $release 'coopnet_plugin_probe.exe') (Join-Path $release 'CP2077CoopNet.dll') }
if ($Loopback) {
    Invoke-Step 'relay loopback' { python (Join-Path $PSScriptRoot 'run_loopback.py') }
}

# Install layout: <game>\red4ext\plugins\CP2077CoopNet\{CP2077CoopNet.dll, Scripts\*.reds}
$stage = Join-Path $root 'dist\red4ext\plugins\CP2077CoopNet'
New-Item -ItemType Directory -Force (Join-Path $stage 'Scripts') | Out-Null
Copy-Item (Join-Path $release 'CP2077CoopNet.dll') $stage -Force
Copy-Item (Join-Path $root 'scripts\CP2077CoopNet\*.reds') (Join-Path $stage 'Scripts') -Force
Write-Host "staged $stage"
