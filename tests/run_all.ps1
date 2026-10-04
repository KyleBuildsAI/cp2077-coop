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

    Every Python run has a time limit (COOP_TEST_TIMEOUT seconds, default 180; the
    whole suite takes about 30 s). A run over the limit has its process tree
    killed, prints "FAIL  timed out ..." and counts as a failed group.

.PARAMETER Only
    Run just these tests, e.g. -Only test_bot.py,test_mods.py. test_relay.py and
    coop_sim30.py run as the relay and movement sim groups; any other name must be
    one of the Python tests in $PythonTests.

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
    "test_payload_schedule.py", "test_make_sandbox.py", "test_marker.py", "test_net_transport.py", "test_v2_integration.py"
)
$ResultPattern = "PASS|FAIL|EXC|Error"
$ManagedVariables = @("PYTHONPATH", "PYTHONDONTWRITEBYTECODE", "PYTHONUNBUFFERED", "COOP_TEST_WORKDIR")
$TimeoutWrapper = Join-Path $TestsDir "run_with_timeout.py"
$TestTimeout = if ($env:COOP_TEST_TIMEOUT) { [int]$env:COOP_TEST_TIMEOUT } else { 180 }
$TimeoutPattern = "^FAIL  timed out"

# Functions print with Write-Output and report through these, never through return
# values (a PowerShell function's return value would be mixed with its output).
$script:FailedGroups = 0
$script:KnownFailures = @()

function Complete-Group {
    param([bool]$Passed)
    if (-not $Passed) { $script:FailedGroups++ }
}

function Invoke-Python {
    # Runs Python with a time limit (0 = none), returns its exit code and every output
    # line (stdout + stderr). Prints nothing. A timeout exits 124 with a FAIL line.
    param([string[]]$Arguments, [int]$TimeoutSeconds = $TestTimeout)
    if ($TimeoutSeconds -gt 0) {
        $lines = @(& $Python $TimeoutWrapper $TimeoutSeconds @Arguments 2>&1 | ForEach-Object { "$_" })
    }
    else {
        $lines = @(& $Python @Arguments 2>&1 | ForEach-Object { "$_" })
    }
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
    $shown = @($Result.Lines | Where-Object { $_ -match $Pattern -or $_ -cmatch $TimeoutPattern })
    if ($shown.Count -eq 0) { $shown = @($Result.Lines | Select-Object -Last $Tail) }
    $shown | ForEach-Object { Write-Output $_ }
}

function Get-Verdict {
    # PASS, KNOWN (only checks tagged [KNOWN ...] failed, no crash) or FAIL. Prints nothing.
    param($Result)
    if ($Result.Code -eq 0) { return "PASS" }
    # a hang is never a known failure, whatever the test printed before it
    if (@($Result.Lines | Where-Object { $_ -cmatch $TimeoutPattern }).Count -gt 0) { return "FAIL" }
    $failLines = @($Result.Lines | Where-Object { $_ -cmatch "^FAIL\b" })
    $untagged = @($failLines | Where-Object { $_ -cnotmatch "\[KNOWN" })
    $crashed = @($Result.Lines | Where-Object { $_ -cmatch "^Traceback" }).Count -gt 0
    if ($failLines.Count -gt 0 -and $untagged.Count -eq 0 -and -not $crashed) { return "KNOWN" }
    return "FAIL"
}

function Test-DependenciesReady {
    # Prints nothing; true when lupa (with LuaJIT 2.1) imports from tests\.deps.
    return (Invoke-Python @("-c", "import lupa.luajit21")).Code -eq 0
}

function Install-Dependencies {
    Write-Output "== dependencies"
    if (-not (Test-DependenciesReady)) {
        Write-Output "installing tests\requirements.txt into tests\.deps"
        # a first-run download can be slow for good reasons
        $install = Invoke-Python @("-m", "pip", "install", "--disable-pip-version-check", "--quiet", "--upgrade",
            "--target", $DepsDir, "-r", (Join-Path $TestsDir "requirements.txt")) -TimeoutSeconds 900
        if ($install.Code -ne 0) { $install.Lines | Select-Object -Last 20 | ForEach-Object { Write-Output $_ } }
    }
    $versions = Invoke-Python @("-c", "import sys, lupa; print('python', sys.version.split()[0], '/ lupa', lupa.__version__)")
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

function Remove-RunSandbox {
    # Keeps the compiler's log, then deletes the per-run sandbox (about 20 MB) even when the run failed.
    param([string]$SandboxDir)
    $compilerLog = Join-Path $SandboxDir "r6\logs\redscript_rCURRENT.log"
    if (Test-Path -LiteralPath $compilerLog -PathType Leaf) {
        Copy-Item -LiteralPath $compilerLog -Destination (Join-Path $WorkDir "redscript_rCURRENT.log")
    }
    try {
        Remove-Item -LiteralPath $SandboxDir -Recurse -Force -ErrorAction Stop
    }
    catch {
        Write-Output "  note: could not remove ${SandboxDir}: $($_.Exception.Message)"
    }
}

function Test-RedscriptCompile {
    Write-Output "== redscript compile (sandbox, no popups)"
    # a sandbox per run: concurrent runs (other worktrees, parallel agents) never compile each other's .reds
    $sandboxDir = Join-Path $WorkDir "scc_sandbox"
    try {
        $sandbox = Invoke-Python @((Join-Path $TestsDir "make_sandbox.py"), "--out", $sandboxDir)
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
    finally {
        if (Test-Path -LiteralPath $sandboxDir) { Remove-RunSandbox $sandboxDir }
    }
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
    Show-Lines $result "PASS|FAIL|err|teleports|moveCommands|log errors"
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

function Invoke-SelectedTest {
    # -Only: each name runs the group that knows its arguments; any other name fails loudly.
    param([string]$Name)
    switch ($Name) {
        "coop_sim30.py" { Test-MovementSim; return }
        "test_relay.py" { Test-Relay; return }
    }
    if ($PythonTests -contains $Name) {
        Test-PythonScript $Name
        return
    }
    Write-Output "== $Name"
    Write-Output ("FAIL  not a runner test: $Name (use: " + (($PythonTests + "coop_sim30.py", "test_relay.py") -join ", ") + ")")
    Complete-Group $false
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
    # a killed test's log keeps the lines it printed before it hung
    $env:PYTHONUNBUFFERED = "1"
    Install-Dependencies
    if (-not (Test-DependenciesReady)) {
        Write-Output "FAIL  could not install tests\requirements.txt into $DepsDir"
        Complete-Group $false
    }
    elseif ($Only.Count -gt 0) {
        foreach ($test in $Only) { Invoke-SelectedTest $test }
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
