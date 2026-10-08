[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$StateRoot = Join-Path $env:LOCALAPPDATA "RankHunter"
$StatePath = Join-Path $StateRoot "setup-state.json"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$StopScript = Join-Path $ScriptRoot "stop-rank-hunter.sh"
$NativeSystemDirectory = if (
    [Environment]::Is64BitOperatingSystem -and -not [Environment]::Is64BitProcess
) {
    Join-Path $env:SystemRoot "Sysnative"
}
else {
    Join-Path $env:SystemRoot "System32"
}
$WslExe = Join-Path $NativeSystemDirectory "wsl.exe"

if (-not (Test-Path $WslExe)) {
    exit 0
}
if (-not (Test-Path $StatePath)) {
    exit 0
}

try {
    $state = Get-Content -Raw $StatePath | ConvertFrom-Json
}
catch {
    exit 0
}

$DistroName = [string]$state.distro
$DistroUser = [string]$state.distro_user
if (-not $DistroName -or -not $DistroUser) {
    exit 0
}

$distros = @(
    (& $WslExe --list --quiet 2>$null) |
        ForEach-Object { ($_ -replace [char]0, "").Trim() } |
        Where-Object { $_ }
)
if ($distros -notcontains $DistroName) {
    exit 0
}

$wslStopScript = (& $WslExe -d $DistroName -u $DistroUser -- wslpath -a $StopScript | Select-Object -Last 1)
if ($LASTEXITCODE -ne 0) {
    exit 0
}

& $WslExe -d $DistroName -u $DistroUser -- bash (($wslStopScript -replace [char]0, "").Trim())
exit 0
