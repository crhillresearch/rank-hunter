[CmdletBinding()]
param(
    [int]$Port = 8501
)

$ErrorActionPreference = "Stop"
$StateRoot = Join-Path $env:LOCALAPPDATA "RankHunter"
$StatePath = Join-Path $StateRoot "setup-state.json"
$LaunchLogPath = Join-Path $StateRoot "launch.log"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$SetupScript = Join-Path $ScriptRoot "RankHunterSetupUi.ps1"
$LaunchScript = Join-Path $ScriptRoot "launch-rank-hunter.sh"

# A 32-bit PowerShell process on 64-bit Windows redirects System32 to SysWOW64,
# where wsl.exe does not exist. Sysnative is the documented escape hatch back
# to the native 64-bit system directory for WOW64 callers.
$NativeSystemDirectory = if (
    [Environment]::Is64BitOperatingSystem -and -not [Environment]::Is64BitProcess
) {
    Join-Path $env:SystemRoot "Sysnative"
}
else {
    Join-Path $env:SystemRoot "System32"
}
$PowerShellExe = Join-Path $NativeSystemDirectory "WindowsPowerShell\v1.0\powershell.exe"
$WslExe = Join-Path $NativeSystemDirectory "wsl.exe"

if (-not (Test-Path $PowerShellExe)) {
    throw "Native Windows PowerShell was not found at $PowerShellExe."
}
if (-not (Test-Path $WslExe)) {
    throw "Windows Subsystem for Linux was not found at $WslExe."
}

New-Item -ItemType Directory -Force -Path $StateRoot | Out-Null
Set-Content -Encoding UTF8 -Path $LaunchLogPath -Value ("[{0}] Rank Hunter launch started." -f [DateTime]::Now.ToString("s"))

function Write-LaunchLog([string]$Message) {
    Add-Content -Encoding UTF8 -Path $LaunchLogPath -Value ("[{0}] {1}" -f [DateTime]::Now.ToString("s"), $Message)
}

trap {
    Write-LaunchLog $_.Exception.Message
    try {
        Add-Type -AssemblyName System.Windows.Forms
        [void][System.Windows.Forms.MessageBox]::Show(
            "Rank Hunter could not start. Rerun the Rank Hunter installer to repair the environment if the problem continues.`n`n$($_.Exception.Message)",
            "Rank Hunter",
            [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Error
        )
    }
    catch {}
    exit 1
}

function Read-State {
    if (-not (Test-Path $StatePath)) { return $null }
    try { return (Get-Content -Raw $StatePath | ConvertFrom-Json) }
    catch { return $null }
}

function Invoke-Repair {
    & $PowerShellExe -NoProfile -ExecutionPolicy Bypass -File $SetupScript -Repair
    if ($LASTEXITCODE -ne 0) {
        throw "Rank Hunter setup did not complete successfully."
    }
}

function Save-LinuxUiLog(
    [string]$Distro,
    [string]$User,
    [string]$LinuxHome
) {
    $uiLog = "$LinuxHome/.rankhunter/ui.log"
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $lines = & $WslExe -d $Distro -u $User -- tail -n 160 $uiLog 2>$null
        if ($LASTEXITCODE -eq 0 -and $lines) {
            Add-Content -Encoding UTF8 -Path $LaunchLogPath -Value "--- Linux UI log ---"
            $lines | Add-Content -Encoding UTF8 -Path $LaunchLogPath
            Add-Content -Encoding UTF8 -Path $LaunchLogPath -Value "--- end Linux UI log ---"
        }
    }
    finally {
        $ErrorActionPreference = $previous
    }
}

$state = Read-State
if (-not $state -or $state.status -ne "ready") {
    Invoke-Repair
    $state = Read-State
}

if (-not $state -or $state.status -ne "ready") {
    throw "Rank Hunter is not ready. Rerun the Rank Hunter installer to repair it."
}

$DistroName = [string]$state.distro
$DistroUser = [string]$state.distro_user
$RepoDirName = [string]$state.repo_dir

if (-not $DistroName -or -not $DistroUser -or -not $RepoDirName) {
    throw "Rank Hunter setup state is incomplete."
}

$distros = @(
    (& $WslExe --list --quiet 2>$null) |
        ForEach-Object { ($_ -replace [char]0, "").Trim() } |
        Where-Object { $_ }
)
if ($distros -notcontains $DistroName) {
    Invoke-Repair
    $state = Read-State
    $DistroName = [string]$state.distro
    $DistroUser = [string]$state.distro_user
    $RepoDirName = [string]$state.repo_dir
}

$linuxHome = ((& $WslExe -d $DistroName -u $DistroUser -- printenv HOME 2>$null | Select-Object -Last 1) -replace [char]0, "").Trim()
if (-not $linuxHome) {
    throw "Could not resolve the Rank Hunter Linux home directory."
}

& $WslExe -d $DistroName -u $DistroUser -- test -x "$linuxHome/$RepoDirName/scripts/run-ui.sh" 1>$null 2>$null
if ($LASTEXITCODE -ne 0) {
    Invoke-Repair
}

$wslLaunchScript = ((& $WslExe -d $DistroName -u $DistroUser -- wslpath -a $LaunchScript | Select-Object -Last 1) -replace [char]0, "").Trim()
if (-not $wslLaunchScript) {
    throw "Could not convert launcher path for WSL."
}

Write-LaunchLog "Starting UI in $DistroName as $DistroUser from $RepoDirName."

# Invoke the Linux helper synchronously. The helper starts Rank Hunter detached
# inside WSL and deliberately keeps this WSL session alive until port $Port is
# listening. This avoids the WSL process-lifetime race seen when a Windows host
# process exits immediately after starting a Linux background process.
$previous = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    & $WslExe -d $DistroName -u $DistroUser -- bash $wslLaunchScript $RepoDirName ([string]$Port)
    $launchExitCode = $LASTEXITCODE
}
finally {
    $ErrorActionPreference = $previous
}

if ($launchExitCode -ne 0) {
    Save-LinuxUiLog -Distro $DistroName -User $DistroUser -LinuxHome $linuxHome
    throw "Rank Hunter exited before the UI became ready (WSL exit $launchExitCode). Details were saved to $LaunchLogPath"
}

Write-LaunchLog "WSL reported the detached UI is listening."

$health = "http://127.0.0.1:$Port/_stcore/health"
$url = "http://localhost:$Port"

$ready = $false
for ($i = 0; $i -lt 90; $i++) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $health -TimeoutSec 2
        if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
            $ready = $true
            break
        }
    }
    catch {}

    Start-Sleep -Seconds 1
}

if (-not $ready) {
    Save-LinuxUiLog -Distro $DistroName -User $DistroUser -LinuxHome $linuxHome
    throw "The Rank Hunter UI did not become ready within 90 seconds. Details were saved to $LaunchLogPath"
}

Write-LaunchLog "UI is healthy at $url."
Start-Process $url
