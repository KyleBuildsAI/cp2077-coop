<# Build/test the actual portable CB77 units and Python relay. No game is launched or changed.
   -NativePlugin also builds the existing Windows transport/plugin and checks DLL loading.
   Full local game-script compile remains tests/run_all.ps1 (CI uses -SkipRedscript).
#>
[CmdletBinding()]
param(
    [ValidateSet('Debug', 'Release')][string]$Configuration = 'Release',
    [string]$BuildDirectory = '',
    [string]$Generator = '',
    [switch]$NativePlugin,
    [string]$SdkSource = ''
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $BuildDirectory) { $BuildDirectory = Join-Path $repoRoot 'build/portable-windows' }
if (-not [IO.Path]::IsPathRooted($BuildDirectory)) { $BuildDirectory = Join-Path $repoRoot $BuildDirectory }

$configure = @('-S', $repoRoot, '-B', $BuildDirectory, '-DCOOP_REFERENCE_ONLY=ON', '-DBUILD_TESTING=ON', '-DCOOP_ENABLE_SANITIZERS=OFF')
if ($Generator) { $configure += @('-G', $Generator) }
if (-not $Generator -or $Generator -like 'Visual Studio*') { $configure += @('-A', 'x64') }
else { $configure += "-DCMAKE_BUILD_TYPE=$Configuration" }
$configure += "-DCOOP_BUILD_NATIVE_PLUGIN=$(if ($NativePlugin) { 'ON' } else { 'OFF' })"
if ($NativePlugin) {
    if (-not $SdkSource) {
        $SdkSource = Join-Path $repoRoot 'plugin/deps/RED4ext.SDK'
        if (-not (Test-Path -LiteralPath (Join-Path $SdkSource 'CMakeLists.txt'))) {
            & (Join-Path $repoRoot 'plugin/tools/fetch_deps.ps1')
            if ($LASTEXITCODE -ne 0) { throw 'Pinned SDK fetch failed' }
        }
    }
    $configure += "-DCOOP_RED4EXT_SDK_DIR=$SdkSource"
}
& cmake @configure
if ($LASTEXITCODE -ne 0) { throw 'Configure failed' }
& cmake --build $BuildDirectory --config $Configuration --parallel
if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
& ctest --test-dir $BuildDirectory -C $Configuration --output-on-failure --no-tests=error
if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }
