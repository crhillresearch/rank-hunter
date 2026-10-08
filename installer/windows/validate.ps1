[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

$required = @(
    "RankHunter.iss",
    "RankHunterSetup.ps1",
    "RankHunterSetupUi.ps1",
    "RankHunterPreflight.ps1",
    "RankHunterLauncher.ps1",
    "RankHunterStop.ps1",
    "RankHunterIcon.svg",
    "make-icon.ps1",
    "bootstrap-prereqs.sh",
    "bootstrap-user.sh",
    "launch-rank-hunter.sh",
    "stop-rank-hunter.sh",
    "build-source.json"
)

foreach ($name in $required) {
    $path = Join-Path $ScriptRoot $name
    if (-not (Test-Path $path)) {
        throw "Missing Windows installer asset: $name"
    }
}

foreach ($name in @("RankHunterSetup.ps1", "RankHunterSetupUi.ps1", "RankHunterPreflight.ps1", "RankHunterLauncher.ps1", "RankHunterStop.ps1", "make-icon.ps1", "build.ps1")) {
    $path = Join-Path $ScriptRoot $name
    $tokens = $null
    $errors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$errors)
    if ($errors.Count -gt 0) {
        $messages = ($errors | ForEach-Object {
            "line $($_.Extent.StartLineNumber), column $($_.Extent.StartColumnNumber): $($_.Message)"
        }) -join "; "
        throw "PowerShell parse failure in ${name}: $messages"
    }
}

$iss = Get-Content -Raw (Join-Path $ScriptRoot "RankHunter.iss")
foreach ($needle in @(
    "RankHunterSetup.ps1",
    "RankHunterSetupUi.ps1",
    "RankHunterPreflight.ps1",
    "RankHunterLauncher.ps1",
    "RankHunter.ico",
    "SetupIconFile=RankHunter.ico",
    "AppPublisher={#RankHunterPublisher}",
    "Run Rank Hunter on close",
    "ArchitecturesInstallIn64BitMode=x64compatible",
    "{autoprograms}\Rank Hunter\Rank Hunter.lnk",
    "bootstrap-prereqs.sh",
    "bootstrap-user.sh",
    "RankHunter-Setup-x64",
    "build-source.json"
)) {
    if (-not $iss.Contains($needle)) {
        throw "RankHunter.iss is missing required contract text: $needle"
    }
}

$setup = Get-Content -Raw (Join-Path $ScriptRoot "RankHunterSetup.ps1")
foreach ($needle in @(
    "wsl.exe",
    "--install",
    "--no-distribution",
    "Start-BitsTransfer",
    "sagemath/sage-binder-env/releases/download",
    "--import",
    "Install-RankHunterShortcut",
    "bootstrap-prereqs.sh",
    "bootstrap-user.sh",
    "setup-progress.json",
    "setup.log",
    "source_bundle",
    "source_commit"
)) {
    if (-not $setup.Contains($needle)) {
        throw "RankHunterSetup.ps1 is missing required contract text: $needle"
    }
}

if ($iss.Contains("[Icons]")) {
    throw "RankHunter.iss must not create Start Menu shortcuts before environment verification."
}

Write-Host "[Rank Hunter] Windows installer static validation passed." -ForegroundColor Green


$source = Get-Content -Raw (Join-Path $ScriptRoot "build-source.json") | ConvertFrom-Json
foreach ($property in @(
    "repo_url", "repo_ref",
    "source_bundle", "source_commit",
    "sage_wsl_version", "sage_wsl_distro", "sage_wsl_url", "sage_wsl_sha256",
    "sage_wsl_size_bytes", "sage_wsl_tar_name"
)) {
    if (-not ($source.PSObject.Properties.Name -contains $property)) {
        throw "build-source.json is missing required property: $property"
    }
}
