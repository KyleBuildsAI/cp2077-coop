# Clones RED4ext.SDK pinned to release 1.0.0 (the SDK version RED4ext 1.30 / game 2.31 plugins use).
# Long paths are enabled for the checkout because the SDK has deep generated-header paths.
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$sdk = Join-Path $root 'deps\RED4ext.SDK'
$pinnedCommit = 'a4a781088a92a8efa890d94fde4efd8985d497c7' # tag 1.0.0

if (-not (Test-Path (Join-Path $sdk 'CMakeLists.txt'))) {
    New-Item -ItemType Directory -Force (Split-Path $sdk) | Out-Null
    git -c core.longpaths=true clone --no-checkout https://github.com/WopsS/RED4ext.SDK.git $sdk
    if ($LASTEXITCODE -ne 0) { throw 'git clone failed' }
    git -C $sdk config core.longpaths true
}
git -C $sdk fetch --tags origin
if ($LASTEXITCODE -ne 0) { throw 'git fetch failed' }
git -C $sdk checkout --quiet $pinnedCommit
if ($LASTEXITCODE -ne 0) { throw "git checkout $pinnedCommit failed" }
Write-Host "RED4ext.SDK at $(git -C $sdk log -1 --format='%h %s')"
