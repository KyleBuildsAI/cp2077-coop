<#
.SYNOPSIS
    Runs every phase 1 tooling test.

.DESCRIPTION
    1. Makes sure lupa (LuaJIT 2.1 for Python) is importable, installing it into .pydeps if needed.
    2. tests\test_sync_audit.py      sync_audit.py on synthetic logs with known error.
    3. tests\test_netprobe_sim.py    netprobe.lua under LuaJIT on an in-memory lossy link.
    4. Unless -SkipNative: exports src\core, tools\coopnet_relay.py and CMakeLists.txt (for the
       version) from the preserved plugin history at -CoreRef with git archive, so an in-progress working tree
       is never compiled or touched. Builds tests\shim into build\shim, then runs
       tests\test_netprobe_relay.py: two probes over the real coopnet::Transport through
       coopnet_relay.py with simulated latency, jitter and loss.

    Test output (audit logs, sync_audit reports) lands in out\.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File run_tests.ps1
    powershell -ExecutionPolicy Bypass -File run_tests.ps1 -SkipNative
    powershell -ExecutionPolicy Bypass -File run_tests.ps1 -RelaySeconds 120
#>
param(
    [switch]$SkipNative,
    [string]$DllProto = (Split-Path $PSScriptRoot -Parent),
    [string]$CoreRef = "5826780c51317984c17a3d49c11a693e3d12f514",
    [int]$RelaySeconds = 30,
    [string]$Python = "python",
    [string]$VsInstance = "C:/Program Files/Microsoft Visual Studio/2022/Community"
)

# Native tools report through exit codes; "Stop" would turn their stderr into terminating errors.
$ErrorActionPreference = "Continue"
$env:PYTHONUNBUFFERED = "1"
$root = $PSScriptRoot
$results = [ordered]@{}

function Invoke-Step {
    param([string]$Name, [scriptblock]$Command)
    Write-Host ""
    Write-Host "===== $Name" -ForegroundColor Cyan
    $started = Get-Date
    $global:LASTEXITCODE = 0
    & $Command | Out-Host
    $code = $global:LASTEXITCODE
    $seconds = [math]::Round(((Get-Date) - $started).TotalSeconds, 1)
    if ($code -eq 0) {
        $results[$Name] = "PASS ($seconds s)"
    } else {
        $results[$Name] = "FAIL (exit $code, $seconds s)"
    }
    return ($code -eq 0)
}

# ---- lupa ---------------------------------------------------------------------------------------
$pydeps = Join-Path $root ".pydeps"
if ($env:PYTHONPATH) {
    $env:PYTHONPATH = "$pydeps;$env:PYTHONPATH"
} else {
    $env:PYTHONPATH = $pydeps
}
& $Python -c "import lupa.luajit21" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "installing lupa into $pydeps"
    & $Python -m pip install --quiet --target $pydeps lupa
    if ($LASTEXITCODE -ne 0) { throw "pip install lupa failed" }
}
& $Python -c "from lupa.luajit21 import LuaRuntime; print('lupa runtime:', LuaRuntime().eval('jit.version'))"
if ($LASTEXITCODE -ne 0) { throw "lupa.luajit21 is not importable" }

# ---- pure Python / LuaJIT tests -------------------------------------------------------------------
$null = Invoke-Step "sync_audit synthetic logs" { & $Python (Join-Path $root "tests\test_sync_audit.py") }
$null = Invoke-Step "netprobe on simulated link" { & $Python (Join-Path $root "tests\test_netprobe_sim.py") }

# ---- real transport + relay ------------------------------------------------------------------------
if (-not $SkipNative) {
    $coreDir = Join-Path $root "build\coresrc"
    $shimBuild = Join-Path $root "build\shim"
    $built = Invoke-Step "build transport shim ($CoreRef)" {
        git -C $DllProto rev-parse --git-dir | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Host "repository with the preserved plugin history not found at $DllProto"
            $global:LASTEXITCODE = 2
            return
        }
        # This historical protocol-1 harness must never export current v2 HEAD.
        # Its pinned original commit remains reachable through the subtree import.
        $expectedCoreDir = [IO.Path]::GetFullPath((Join-Path $root 'build\coresrc'))
        if ([IO.Path]::GetFullPath($coreDir) -ne $expectedCoreDir) { throw 'Unexpected core export path.' }
        if (Test-Path -LiteralPath $coreDir) {
            if ((Resolve-Path -LiteralPath $coreDir).Path -ne $expectedCoreDir) { throw 'Core export path resolves outside the expected directory.' }
            Remove-Item -LiteralPath $coreDir -Recurse -Force
        }
        New-Item -ItemType Directory -Force $coreDir | Out-Null
        $archive = Join-Path $root "build\coresrc.tar"
        git -C $DllProto archive --format=tar --output $archive $CoreRef src/core tools/coopnet_relay.py CMakeLists.txt
        if ($LASTEXITCODE -ne 0) { return }
        tar -x -f $archive -C $coreDir
        if ($LASTEXITCODE -ne 0) { return }
        $configure = @("-S", (Join-Path $root "tests\shim"), "-B", $shimBuild, "-G", "Visual Studio 17 2022", "-A", "x64",
            "-DCOOPNET_CORE_SRC=$($coreDir -replace '\\', '/')/src")
        if (Test-Path $VsInstance) { $configure += "-DCMAKE_GENERATOR_INSTANCE=$VsInstance" }
        cmake @configure
        if ($LASTEXITCODE -ne 0) { return }
        cmake --build $shimBuild --config Release --parallel
    }
    if ($built) {
        $env:COOPNET_SHIM = Join-Path $shimBuild "Release\coopnet_shim.dll"
        $env:COOPNET_RELAY = Join-Path $coreDir "tools\coopnet_relay.py"
        $env:NETPROBE_RELAY_SECONDS = "$RelaySeconds"
        $null = Invoke-Step "netprobe over real transport + relay" {
            & $Python (Join-Path $root "tests\test_netprobe_relay.py")
        }
    }
}

# ---- summary ---------------------------------------------------------------------------------------
Write-Host ""
Write-Host "===== SUMMARY" -ForegroundColor Cyan
$failed = 0
foreach ($entry in $results.GetEnumerator()) {
    Write-Host ("  {0,-45} {1}" -f $entry.Key, $entry.Value)
    if ($entry.Value -notlike "PASS*") { $failed++ }
}
if ($SkipNative) { Write-Host "  (native transport test skipped by -SkipNative)" }
if ($failed -eq 0) {
    Write-Host "ALL PASS" -ForegroundColor Green
    exit 0
}
Write-Host "$failed step(s) failed" -ForegroundColor Red
exit 1
