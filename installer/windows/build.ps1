[CmdletBinding()]
param(
    [string]$Version = "0.9.2-preview",
    [string]$RepoUrl = "https://github.com/crhillresearch/rank-hunter.git",
    [string]$RepoRef = "main",
    [string]$SourceBundle = "",
    [string]$SourceCommit = "",
    [string]$SageWslVersion = "10.10",
    [string]$SageWslDistro = "RankHunter-Sage-10.10",
    [string]$SageWslUrl = "https://github.com/sagemath/sage-binder-env/releases/download/v10.10/sagemath-10.10-wsl.zip",
    [string]$SageWslSha256 = "87d2f2542a48ab0bee07defcad196335576b3c1b621233f5461a9aedd44edff6",
    [Int64]$SageWslSizeBytes = 1376792539,
    [string]$SageWslTarName = "sagemath-10.10-wsl.tar",
    [switch]$InstallInno
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$IssPath = Join-Path $ScriptRoot "RankHunter.iss"
$BuildSourcePath = Join-Path $ScriptRoot "build-source.json"
$IconBuildScript = Join-Path $ScriptRoot "make-icon.ps1"

if ($SourceBundle) {
    $SourceBundle = (Resolve-Path $SourceBundle).Path
    if (-not $SourceCommit) {
        throw "SourceBundle requires SourceCommit."
    }
}

function Write-BuildSourceManifest {
    $manifest = [ordered]@{
        repo_url = $RepoUrl
        repo_ref = $RepoRef
        source_bundle = $(if ($SourceBundle) { "source\rank-hunter.bundle" } else { "" })
        source_commit = $SourceCommit
        sage_wsl_version = $SageWslVersion
        sage_wsl_distro = $SageWslDistro
        sage_wsl_url = $SageWslUrl
        sage_wsl_sha256 = $SageWslSha256
        sage_wsl_size_bytes = $SageWslSizeBytes
        sage_wsl_tar_name = $SageWslTarName
    } | ConvertTo-Json

    [IO.File]::WriteAllText(
        $BuildSourcePath,
        $manifest + [Environment]::NewLine,
        [Text.UTF8Encoding]::new($false)
    )
}

function Set-UnixLineEndings {
    foreach ($name in @(
        "bootstrap-prereqs.sh",
        "bootstrap-user.sh",
        "launch-rank-hunter.sh",
        "stop-rank-hunter.sh"
    )) {
        $path = Join-Path $ScriptRoot $name
        $text = [IO.File]::ReadAllText($path)
        $normalized = $text.Replace("`r`n", "`n").Replace("`r", "`n")
        [IO.File]::WriteAllText(
            $path,
            $normalized,
            [Text.UTF8Encoding]::new($false)
        )
    }
}

function Find-Iscc {
    $programFilesX86 = [Environment]::GetFolderPath("ProgramFilesX86")
    $programFiles = [Environment]::GetFolderPath("ProgramFiles")
    $candidates = @(
        (Join-Path $programFilesX86 "Inno Setup 6\ISCC.exe"),
        (Join-Path $programFiles "Inno Setup 6\ISCC.exe")
    ) | Where-Object { $_ -and (Test-Path $_) }

    $candidateList = @($candidates)
    if ($candidateList.Count -gt 0) {
        return [string]$candidateList[0]
    }

    $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    return $null
}

$iscc = Find-Iscc
if (-not $iscc -and $InstallInno) {
    if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
        throw "Inno Setup is missing and winget.exe is unavailable."
    }
    Write-Host "[Rank Hunter] Installing Inno Setup 6 with winget..." -ForegroundColor Cyan
    & winget.exe install --id JRSoftware.InnoSetup --exact --silent --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        throw "winget failed to install Inno Setup."
    }
    $iscc = Find-Iscc
}

if (-not $iscc) {
    throw "Inno Setup 6 was not found. Install it or rerun build.ps1 -InstallInno."
}

Write-BuildSourceManifest
Set-UnixLineEndings

Write-Host "[Rank Hunter] Generating branded Windows icon..." -ForegroundColor Cyan
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $IconBuildScript
if ($LASTEXITCODE -ne 0) {
    throw "Rank Hunter Windows icon generation failed."
}

Write-Host "[Rank Hunter] Compiling Windows installer..." -ForegroundColor Cyan
Write-Host "[Rank Hunter] Package source: $RepoUrl @ $RepoRef" -ForegroundColor Cyan
Write-Host "[Rank Hunter] SageMath WSL environment: $SageWslVersion (~$([Math]::Round($SageWslSizeBytes / 1GB, 2)) GB downloaded only if needed)" -ForegroundColor Cyan

$innoArgs = @("/DRankHunterVersion=$Version")
if ($SourceBundle) {
    Write-Host "[Rank Hunter] Bundled application source: $SourceCommit" -ForegroundColor Cyan
    $innoArgs += "/DRankHunterSourceBundle=$SourceBundle"
}

& $iscc @innoArgs $IssPath
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup compilation failed."
}

$output = Join-Path $ScriptRoot "dist\RankHunter-Setup-x64.exe"
if (-not (Test-Path $output)) {
    throw "Installer compilation succeeded but output was not found: $output"
}

$hash = Get-FileHash -Algorithm SHA256 -Path $output
Write-Host ""
Write-Host "[Rank Hunter] Windows installer ready:" -ForegroundColor Green
Write-Host "  $output"
Write-Host "  SHA256 $($hash.Hash)"
