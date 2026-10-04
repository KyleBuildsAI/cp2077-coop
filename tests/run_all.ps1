<#
.SYNOPSIS
    Runs every offline check for the CP2077 Coop package. No game is launched.

.DESCRIPTION
    Groups: LuaJIT load of init.lua, redscript compile in a sandbox, the Python
    tests, the movement sim, the relay test and a syntax check of coop-tools.
    The exit code is the number of failed groups (0 = all green).

    The first run installs tests\requirements.txt into tests\.deps (pip --target,
    gitignored). The redscript sandbox is built by make_sandbox.py from the game
    folder in COOP_GAME_DIR (read only). Every Python test runs in its own fresh
    folder under %TEMP%, because the mod's role.txt and stats files are relative
    to the working directory.

    A test that fails only on checks tagged [KNOWN ...] is reported as a known,
    tracked failure and not counted.

.PARAMETER Only
    Run just these Python tests, e.g. -Only test_bot.py,test_mods.py.

.PARAMETER KeepWorkDir
    Keep the per-run folder with every test's full log (always kept on failure).

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File tests\run_all.ps1
#>
[CmdletBinding()]
param(
    [string[]]$Only = @(),
    [switch]$KeepWorkDir
)

$ErrorActionPreference = "Continue"
# powershell -File passes "-Only a.py,b.py" as one string
$Only = @($Only | ForEach-Object { $_ -split "," } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$TestsDir = $PSScriptRoot
$RepoDir = Split-Path -Parent $TestsDir
$LuaScript = Join-Path $RepoDir "bin\x64\plugins\cyber_engine_tweaks\mods\CP2077Coop\init.lua"
$ToolsDir = Join-Path $RepoDir "coop-tools"
$DepsDir = Join-Path $TestsDir ".deps"
$Python = if ($env:COOP_PYTHON) { $env:COOP_PYTHON } else { "python" }
$PythonTests = @(
    "test_state_sync.py", "test_two_players.py", "test_bot.py", "test_combat.py", "test_mods.py",
    "test_live_bugs.py", "test_join.py", "test_timing.py", "test_avatar.py", "test_robustness.py", "test_diagnostics.py",
    "test_payload_schedule.py"
)
$ResultPattern = "PASS|FAIL|EXC|Error"
$ManagedVariables = @("PYTHONPATH", "PYTHONDONTWRITEBYTECODE", "COOP_TEST_WORKDIR")

# Functions print with Write-Output and report through these, never through return
# values (a PowerShell function's return value would be mixed with its output).
$script:FailedGroups = 0
$script:KnownFailures = @()

function Complete-Group {
    param([bool]$Passed)
    if (-not $Passed) { $script:FailedGroups++ }
}

function Invoke-Python {
    # Runs Python, returns its exit code and every output line (stdout + stderr). Prints nothing.
    param([string[]]$Arguments)
    $lines = @(& $Python @Arguments 2>&1 | ForEach-Object { "$_" })
    return [pscustomobject]@{ Code = $LASTEXITCODE; Lines = $lines }
}

function Invoke-InWorkFolder {
    # Runs one Python script in a fresh working folder (role.txt etc. are cwd-relative)
    # and keeps its full output as <name>.log in the run folder. Prints nothing.
    param([string]$Name, [string[]]$Arguments)
    $folder = Join-Path $WorkDir $Name
    New-Item -ItemType Directory -Force -Path $folder | Out-Null
    $env:COOP_TEST_WORKDIR = $folder
    Push-Location -LiteralPath $folder
    try {
        $result = Invoke-Python $Arguments
    }
    finally {
        Pop-Location
    }
    Set-Content -LiteralPath (Join-Path $WorkDir "$Name.log") -Value $result.Lines -Encoding UTF8
    return $result
}

function Show-Lines {
    # Prints the lines that match $Pattern; when none match, the tail (so a crash is visible).
    param($Result, [string]$Pattern, [int]$Tail = 25)
    $shown = @($Result.Lines | Where-Object { $_ -match $Pattern })
    if ($shown.Count -eq 0) { $shown = @($Result.Lines | Select-Object -Last $Tail) }
    $shown | ForEach-Object { Write-Output $_ }
}

function Get-Verdict {
    # PASS, KNOWN (only checks tagged [KNOWN ...] failed, no crash) or FAIL. Prints nothing.
    param($Result)
    if ($Result.Code -eq 0) { return "PASS" }
    $failLines = @($Result.Lines | Where-Object { $_ -cmatch "^FAIL\b" })
    $untagged = @($failLines | Where-Object { $_ -cnotmatch "\[KNOWN" })
    $crashed = @($Result.Lines | Where-Object { $_ -cmatch "^Traceback" }).Count -gt 0
    if ($failLines.Count -gt 0 -and $untagged.Count -eq 0 -and -not $crashed) { return "KNOWN" }
    return "FAIL"
}

function Test-DependenciesReady {
    # Prints nothing; true when lupa and luaparser import from tests\.deps.
    return (Invoke-Python @("-c", "import lupa.luajit21, luaparser")).Code -eq 0
}

function Install-Dependencies {
    Write-Output "== dependencies"
    if (-not (Test-DependenciesReady)) {
        Write-Output "installing tests\requirements.txt into tests\.deps"
        $install = Invoke-Python @("-m", "pip", "install", "--disable-pip-version-check", "--quiet", "--upgrade",
            "--target", $DepsDir, "-r", (Join-Path $TestsDir "requirements.txt"))
        if ($install.Code -ne 0) { $install.Lines | Select-Object -Last 20 | ForEach-Object { Write-Output $_ } }
    }
    $versions = Invoke-Python @("-c", "import sys, lupa, luaparser; print('python', sys.version.split()[0], '/ lupa', lupa.__version__)")
    $versions.Lines | ForEach-Object { Write-Output $_ }
}

function Test-LuaLoad {
    Write-Output "== LuaJIT load"
    $code = "import sys; from lupa.luajit21 import LuaRuntime; lua = LuaRuntime(); " +
        "lua.execute('function registerForEvent() end; function registerHotkey() end'); " +
        "lua.execute(open(sys.argv[1], encoding='utf-8').read()); print('LuaJIT LOAD OK')"
    $result = Invoke-Python @("-c", $code, $LuaScript)
    Show-Lines $result "LOAD OK|Error"
    Complete-Group ($result.Code -eq 0)
}

function Test-RedscriptCompile {
    Write-Output "== redscript compile (sandbox, no popups)"
    $sandbox = Invoke-Python @((Join-Path $TestsDir "make_sandbox.py"))
    $sandbox.Lines | ForEach-Object { Write-Output "  $_" }
    if ($sandbox.Code -ne 0) {
        Complete-Group $false
        return
    }
    $compile = Invoke-Python @((Join-Path $ToolsDir "scc_check.py"), $sandbox.Lines[-1])
    Set-Content -LiteralPath (Join-Path $WorkDir "redscript.log") -Value $compile.Lines -Encoding UTF8
    if ($compile.Code -eq 0) { Show-Lines $compile "OK:|WARN" } else { $compile.Lines | ForEach-Object { Write-Output $_ } }
    Complete-Group ($compile.Code -eq 0)
}

function Test-PythonScript {
    param([string]$Test)
    Write-Output "== $Test"
    $path = Join-Path $TestsDir $Test
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        Write-Output "FAIL  no such test: $path"
        Complete-Group $false
        return
    }
    $result = Invoke-InWorkFolder ([IO.Path]::GetFileNameWithoutExtension($Test)) @($path, $LuaScript)
    Show-Lines $result $ResultPattern
    # a tag left on a passing check would hide its next regression
    $staleTags = @($result.Lines | Where-Object { $_ -cmatch "^PASS\b" -and $_ -cmatch "\[KNOWN" })
    foreach ($line in $staleTags) { Write-Output "  note: tagged check now passes, remove its [KNOWN] tag: $line" }
    $verdict = Get-Verdict $result
    if ($verdict -eq "KNOWN") {
        Write-Output "  (only [KNOWN] checks failed: tracked, not counted)"
        $script:KnownFailures += $Test
    }
    Complete-Group ($verdict -ne "FAIL")
}

function Test-MovementSim {
    Write-Output "== movement sim"
    $result = Invoke-InWorkFolder "coop_sim30" @((Join-Path $TestsDir "coop_sim30.py"), "current", $LuaScript)
    Show-Lines $result "err|teleports|moveCommands|log errors"
    Complete-Group ($result.Code -eq 0)
}

function Test-Relay {
    Write-Output "== relay"
    $result = Invoke-InWorkFolder "test_relay" @((Join-Path $TestsDir "test_relay.py"), (Join-Path $ToolsDir "coop_relay.py"))
    $result.Lines | Select-Object -Last 1 | ForEach-Object { Write-Output $_ }
    Complete-Group ($result.Code -eq 0)
}

function Test-ToolsSyntax {
    # Compiles each coop-tools script in memory (py_compile would write __pycache__ into the repo).
    Write-Output "== python tools compile"
    $code = "import sys; compile(open(sys.argv[1], encoding='utf-8-sig').read(), sys.argv[1], 'exec')"
    $broken = 0
    foreach ($tool in Get-ChildItem -LiteralPath $ToolsDir -Filter "*.py") {
        $result = Invoke-Python @("-c", $code, $tool.FullName)
        if ($result.Code -ne 0) {
            Write-Output "FAIL compile $($tool.Name)"
            $result.Lines | ForEach-Object { Write-Output "  $_" }
            $broken++
        }
    }
    if ($broken -eq 0) { Write-Output "OK" }
    Complete-Group ($broken -eq 0)
}

if (-not (Get-Command $Python -ErrorAction SilentlyContinue)) {
    Write-Output "python not found (put it on PATH or set COOP_PYTHON)"
    exit 1
}

$savedEnvironment = @{}
foreach ($name in $ManagedVariables) { $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process") }
$WorkDir = Join-Path ([IO.Path]::GetTempPath()) ("cp2077coop_tests_{0}_{1}" -f (Get-Date -Format "yyyyMMdd_HHmmss"), $PID)
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
$clock = [Diagnostics.Stopwatch]::StartNew()

try {
    $env:PYTHONPATH = $DepsDir
    $env:PYTHONDONTWRITEBYTECODE = "1"
    Install-Dependencies
    if (-not (Test-DependenciesReady)) {
        Write-Output "FAIL  could not install tests\requirements.txt into $DepsDir"
        Complete-Group $false
    }
    elseif ($Only.Count -gt 0) {
        foreach ($test in $Only) { Test-PythonScript $test }
    }
    else {
        Test-LuaLoad
        Test-RedscriptCompile
        foreach ($test in $PythonTests) { Test-PythonScript $test }
        Test-MovementSim
        Test-Relay
        Test-ToolsSyntax
    }
}
finally {
    foreach ($name in $ManagedVariables) { [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], "Process") }
}

if ($script:KnownFailures.Count -gt 0) {
    Write-Output ("known, tracked failures (not counted): " + ($script:KnownFailures -join ", "))
}
if ($script:FailedGroups -gt 0 -or $KeepWorkDir) {
    Write-Output "full logs: $WorkDir"
}
else {
    try {
        Remove-Item -LiteralPath $WorkDir -Recurse -Force -ErrorAction Stop
    }
    catch {
        Write-Output "note: could not remove ${WorkDir}: $($_.Exception.Message)"
    }
}
Write-Output ("TOTAL FAILED GROUPS: {0}  ({1:N0} s)" -f $script:FailedGroups, $clock.Elapsed.TotalSeconds)
exit $script:FailedGroups
