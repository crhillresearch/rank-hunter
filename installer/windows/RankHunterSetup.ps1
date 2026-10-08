[CmdletBinding()]
param(
    [string]$RepoUrl = "https://github.com/crhillresearch/rank-hunter.git",
    [string]$RepoRef = "main",
    [string]$RepoDirName = "rank-hunter",
    [string]$ScienceUrl = "https://github.com/sagemath/sage-binder-env/releases/download/v10.10/sagemath-10.10-wsl.zip",
    [string]$ScienceSha256 = "87d2f2542a48ab0bee07defcad196335576b3c1b621233f5461a9aedd44edff6",
    [string]$ScienceVersion = "10.10",
    [Int64]$ScienceSizeBytes = 1376792539,
    [string]$ScienceDistroName = "RankHunter-Sage-10.10",
    [string]$ScienceTarName = "sagemath-10.10-wsl.tar",
    [string]$SourceBundle = "",
    [string]$SourceCommit = "",
    [string]$ProgressPath = "",
    [string]$LogPath = "",
    [switch]$CustomSelection,
    [switch]$InstallWsl,
    [switch]$InstallScience,
    [switch]$InstallRankHunter,
    [switch]$Quiet,
    [switch]$Repair
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$StateRoot = Join-Path $env:LOCALAPPDATA "RankHunter"
$StatePath = Join-Path $StateRoot "setup-state.json"
$DownloadRoot = Join-Path $StateRoot "downloads"
$WslRoot = Join-Path $StateRoot "wsl"
if (-not $ProgressPath) { $ProgressPath = Join-Path $StateRoot "setup-progress.json" }
if (-not $LogPath) { $LogPath = Join-Path $StateRoot "setup.log" }
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$BuildSourcePath = Join-Path $ScriptRoot "build-source.json"

if (-not $CustomSelection) {
    $InstallWsl = $true
    $InstallScience = $true
    $InstallRankHunter = $true
}

if (Test-Path $BuildSourcePath) {
    $buildSource = Get-Content -Raw $BuildSourcePath | ConvertFrom-Json
    if (-not $PSBoundParameters.ContainsKey("RepoUrl") -and $buildSource.repo_url) {
        $RepoUrl = [string]$buildSource.repo_url
    }
    if (-not $PSBoundParameters.ContainsKey("RepoRef") -and $buildSource.repo_ref) {
        $RepoRef = [string]$buildSource.repo_ref
    }
    if (-not $PSBoundParameters.ContainsKey("ScienceUrl") -and $buildSource.sage_wsl_url) {
        $ScienceUrl = [string]$buildSource.sage_wsl_url
    }
    if (-not $PSBoundParameters.ContainsKey("ScienceSha256") -and $buildSource.sage_wsl_sha256) {
        $ScienceSha256 = [string]$buildSource.sage_wsl_sha256
    }
    if (-not $PSBoundParameters.ContainsKey("ScienceVersion") -and $buildSource.sage_wsl_version) {
        $ScienceVersion = [string]$buildSource.sage_wsl_version
    }
    if (-not $PSBoundParameters.ContainsKey("ScienceSizeBytes") -and $buildSource.sage_wsl_size_bytes) {
        $ScienceSizeBytes = [Int64]$buildSource.sage_wsl_size_bytes
    }
    if (-not $PSBoundParameters.ContainsKey("ScienceDistroName") -and $buildSource.sage_wsl_distro) {
        $ScienceDistroName = [string]$buildSource.sage_wsl_distro
    }
    if (-not $PSBoundParameters.ContainsKey("ScienceTarName") -and $buildSource.sage_wsl_tar_name) {
        $ScienceTarName = [string]$buildSource.sage_wsl_tar_name
    }
    if (-not $PSBoundParameters.ContainsKey("SourceBundle") -and $buildSource.source_bundle) {
        $SourceBundle = [string]$buildSource.source_bundle
    }
    if (-not $PSBoundParameters.ContainsKey("SourceCommit") -and $buildSource.source_commit) {
        $SourceCommit = [string]$buildSource.source_commit
    }
}

function Write-SetupProgress(
    [string]$Stage,
    [string]$Detail = "",
    [int]$Percent = -1
) {
    # Progress reporting is UI telemetry, not installation state. Never let a
    # transient reader/writer sharing violation abort an otherwise valid setup.
    New-Item -ItemType Directory -Force -Path $StateRoot | Out-Null
    $payload = [ordered]@{
        stage = $Stage
        detail = $Detail
        percent = $Percent
        updated_at = [DateTime]::UtcNow.ToString("o")
    } | ConvertTo-Json

    for ($attempt = 1; $attempt -le 10; $attempt++) {
        try {
            Set-Content -Encoding UTF8 -Path $ProgressPath -Value $payload -ErrorAction Stop
            return
        }
        catch [System.IO.IOException] {
            if ($attempt -lt 10) {
                Start-Sleep -Milliseconds 50
                continue
            }
        }
        catch [System.UnauthorizedAccessException] {
            if ($attempt -lt 10) {
                Start-Sleep -Milliseconds 50
                continue
            }
        }
    }

    # Setup correctness is recorded in setup-state.json and setup.log. A busy
    # progress file must not turn successful environment/app work into failure.
}

function Write-SetupLog([string]$Message) {
    New-Item -ItemType Directory -Force -Path $StateRoot | Out-Null
    Add-Content -Encoding UTF8 -Path $LogPath -Value $Message
}

function Write-Step(
    [string]$Message,
    [string]$Detail = "",
    [int]$Percent = -1
) {
    Write-SetupProgress -Stage $Message -Detail $Detail -Percent $Percent
    Write-SetupLog "[Rank Hunter] $Message $Detail"
    if (-not $Quiet) {
        Write-Host ""
        Write-Host "[Rank Hunter] $Message" -ForegroundColor Cyan
        if ($Detail) { Write-Host $Detail }
    }
}

function Save-State(
    [string]$Status,
    [string]$Detail,
    [string]$Distro = "",
    [string]$DistroUser = "",
    [string]$SciencePython = ""
) {
    New-Item -ItemType Directory -Force -Path $StateRoot | Out-Null
    [ordered]@{
        status = $Status
        detail = $Detail
        distro = $Distro
        distro_user = $DistroUser
        science_python = $SciencePython
        science_version = $ScienceVersion
        repo_url = $RepoUrl
        repo_ref = $RepoRef
        repo_dir = $RepoDirName
        updated_at = [DateTime]::UtcNow.ToString("o")
    } | ConvertTo-Json | Set-Content -Encoding UTF8 -Path $StatePath
}

function Get-WslDistros {
    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
        return @()
    }

    $raw = & wsl.exe --list --quiet 2>$null
    if ($LASTEXITCODE -ne 0) {
        return @()
    }

    return @(
        $raw |
            ForEach-Object { ($_ -replace [char]0, "").Trim() } |
            Where-Object { $_ }
    )
}

function Test-WslReady {
    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
        return $false
    }
    & wsl.exe --status 1>$null 2>$null
    return $LASTEXITCODE -eq 0
}

function Install-WslPlatform {
    if (-not $InstallWsl) {
        throw "WSL2 is required but its installation was unchecked."
    }

    Write-Step "Installing WSL2" "Windows may request administrator approval. A restart can be required." 8
    $process = Start-Process -FilePath "wsl.exe" -ArgumentList @("--install", "--no-distribution") -Verb RunAs -Wait -PassThru

    if ($process.ExitCode -ne 0 -and $process.ExitCode -ne 3010) {
        throw "Windows could not install WSL2 (exit $($process.ExitCode))."
    }

    if ($process.ExitCode -eq 3010 -or -not (Test-WslReady)) {
        Save-State "reboot-required" "Windows must restart before Rank Hunter setup can continue."
        Write-SetupProgress "Windows restart required" "Restart Windows, then run the Rank Hunter installer again." 100
        exit 0
    }
}

function Invoke-WslProbe(
    [string]$Distro,
    [string]$User,
    [string[]]$Command
) {
    $wslArgs = @("-d", $Distro, "-u", $User, "--") + $Command
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & wsl.exe @wslArgs 2>$null
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previous
    }
    return [pscustomobject]@{ ExitCode = $exitCode; Output = @($output) }
}

function Resolve-DistroUser([string]$Distro) {
    $defaultUid = Invoke-WslProbe -Distro $Distro -User "root" -Command @("id", "-u")
    $defaultUser = Invoke-WslProbe -Distro $Distro -User "root" -Command @("id", "-un")
    if ($defaultUid.ExitCode -eq 0 -and $defaultUser.ExitCode -eq 0) {
        $uid = (($defaultUid.Output | Select-Object -Last 1) -replace [char]0, "").Trim()
        $user = (($defaultUser.Output | Select-Object -Last 1) -replace [char]0, "").Trim()
        if ($uid -match '^[0-9]+$' -and [int]$uid -ne 0 -and $user) { return $user }
    }

    $passwdProbe = Invoke-WslProbe -Distro $Distro -User "root" -Command @("getent", "passwd", "1000")
    if ($passwdProbe.ExitCode -eq 0) {
        $passwd = (($passwdProbe.Output | Select-Object -Last 1) -replace [char]0, "").Trim()
        if ($passwd) {
            $parts = $passwd.Split(":")
            if ($parts.Count -ge 1 -and $parts[0] -and $parts[0] -ne "root") { return $parts[0] }
        }
    }
    return ""
}

function Get-SciencePython([string]$Distro, [string]$User) {
    $homeProbe = Invoke-WslProbe -Distro $Distro -User $User -Command @("printenv", "HOME")
    $linuxHome = ""
    if ($homeProbe.ExitCode -eq 0) {
        $linuxHome = (($homeProbe.Output | Select-Object -Last 1) -replace [char]0, "").Trim()
    }

    $candidates = @(
        "/opt/rankhunter/miniforge3/envs/sage/bin/python",
        "/opt/conda/envs/sage/bin/python",
        "/usr/bin/python3",
        "/usr/bin/python"
    )
    if ($linuxHome) {
        $candidates = @(
            "$linuxHome/miniforge3/envs/sage/bin/python",
            "$linuxHome/mambaforge/envs/sage/bin/python",
            "$linuxHome/miniconda3/envs/sage/bin/python"
        ) + $candidates
    }

    foreach ($python in $candidates) {
        $exists = Invoke-WslProbe -Distro $Distro -User $User -Command @("test", "-x", $python)
        if ($exists.ExitCode -ne 0) { continue }
        $probe = Invoke-WslProbe -Distro $Distro -User $User -Command @($python, "-c", "import sage.all,sys; print(sys.executable)")
        if ($probe.ExitCode -eq 0) {
            $path = (($probe.Output | Select-Object -Last 1) -replace [char]0, "").Trim()
            if ($path) { return $path }
        }
    }

    foreach ($sage in @("/usr/bin/sage", "/usr/local/bin/sage")) {
        $exists = Invoke-WslProbe -Distro $Distro -User $User -Command @("test", "-x", $sage)
        if ($exists.ExitCode -ne 0) { continue }
        $probe = Invoke-WslProbe -Distro $Distro -User $User -Command @($sage, "-python", "-c", "import sage.all,sys; print(sys.executable)")
        if ($probe.ExitCode -eq 0) {
            $path = (($probe.Output | Select-Object -Last 1) -replace [char]0, "").Trim()
            if ($path) { return $path }
        }
    }

    $whichProbe = Invoke-WslProbe -Distro $Distro -User $User -Command @("which", "sage")
    if ($whichProbe.ExitCode -eq 0) {
        $sagePath = (($whichProbe.Output | Select-Object -Last 1) -replace [char]0, "").Trim()
        if ($sagePath) {
            $probe = Invoke-WslProbe -Distro $Distro -User $User -Command @($sagePath, "-python", "-c", "import sage.all,sys; print(sys.executable)")
            if ($probe.ExitCode -eq 0) {
                $path = (($probe.Output | Select-Object -Last 1) -replace [char]0, "").Trim()
                if ($path) { return $path }
            }
        }
    }
    return ""
}

function Find-ScienceEnvironment {
    $distros = @(Get-WslDistros)
    $ordered = New-Object System.Collections.Generic.List[string]

    foreach ($preferred in @($ScienceDistroName, "SageMath-$ScienceVersion")) {
        if ($preferred -and $distros -contains $preferred -and -not $ordered.Contains($preferred)) {
            $ordered.Add($preferred)
        }
    }
    foreach ($distro in $distros) {
        if (-not $ordered.Contains($distro)) {
            $ordered.Add($distro)
        }
    }

    foreach ($distro in $ordered) {
        $user = Resolve-DistroUser $distro
        if (-not $user) { continue }

        $python = Get-SciencePython -Distro $distro -User $user
        if (-not $python) { continue }

        return [pscustomobject]@{
            Distro = $distro
            User = $user
            Python = $python
        }
    }

    return $null
}

function Download-ScienceImage([string]$Destination) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Destination) | Out-Null

    if (Test-Path $Destination) {
        $existingHash = (Get-FileHash -Algorithm SHA256 -Path $Destination).Hash.ToLowerInvariant()
        if ($existingHash -eq $ScienceSha256.ToLowerInvariant()) {
            Write-SetupLog "[Rank Hunter] Reusing verified SageMath download: $Destination"
            return
        }
        Remove-Item -Force $Destination
    }

    Write-Step "Downloading SageMath scientific environment" ("About {0:N1} GB; usually 5-15 minutes depending on connection." -f ($ScienceSizeBytes / 1GB)) 15

    Import-Module BitsTransfer -ErrorAction Stop
    $job = Start-BitsTransfer -Source $ScienceUrl -Destination $Destination -Asynchronous -DisplayName "Rank Hunter scientific environment"
    try {
        while ($true) {
            $job = Get-BitsTransfer -Id $job.Id
            switch ($job.JobState) {
                "Transferred" {
                    Complete-BitsTransfer -BitsJob $job
                    break
                }
                "Error" {
                    throw "Scientific environment download failed: $($job.ErrorDescription)"
                }
                "TransientError" {
                    Write-SetupLog "[Rank Hunter] BITS transient error: $($job.ErrorDescription)"
                }
            }

            if ($job.BytesTotal -gt 0) {
                $downloadPercent = [Math]::Min(99, [Math]::Floor(100 * $job.BytesTransferred / $job.BytesTotal))
                $overallPercent = 15 + [Math]::Floor(40 * $downloadPercent / 100)
                $doneGB = $job.BytesTransferred / 1GB
                $totalGB = $job.BytesTotal / 1GB
                Write-SetupProgress "Downloading SageMath scientific environment" ("{0}%  ({1:N2} / {2:N2} GB)" -f $downloadPercent, $doneGB, $totalGB) $overallPercent
            }
            Start-Sleep -Milliseconds 750
        }
    }
    catch {
        try { Remove-BitsTransfer -BitsJob $job -Confirm:$false -ErrorAction SilentlyContinue } catch {}
        throw
    }

    $actual = (Get-FileHash -Algorithm SHA256 -Path $Destination).Hash.ToLowerInvariant()
    if ($actual -ne $ScienceSha256.ToLowerInvariant()) {
        Remove-Item -Force $Destination -ErrorAction SilentlyContinue
        throw "Downloaded SageMath environment failed SHA256 verification."
    }
}

function Import-ScienceEnvironment {
    if (-not $InstallScience) {
        throw "A SageMath scientific environment is required but its installation was unchecked."
    }
    if (-not (Test-WslReady)) {
        throw "WSL2 is not ready, so the scientific environment cannot be installed."
    }

    New-Item -ItemType Directory -Force -Path $DownloadRoot | Out-Null
    New-Item -ItemType Directory -Force -Path $WslRoot | Out-Null

    $zipPath = Join-Path $DownloadRoot ("sagemath-{0}-wsl.zip" -f $ScienceVersion)
    $extractPath = Join-Path $DownloadRoot ("sagemath-{0}-expanded" -f $ScienceVersion)
    $distroPath = Join-Path $WslRoot $ScienceDistroName

    $distros = @(Get-WslDistros)
    if ($distros -contains $ScienceDistroName) {
        if (Test-Path $distroPath) {
            Write-Step "Repairing SageMath scientific environment" "Removing an incomplete Rank Hunter-owned WSL environment before reinstalling it." 12
            & wsl.exe --terminate $ScienceDistroName 1>$null 2>$null
            & wsl.exe --unregister $ScienceDistroName
            if ($LASTEXITCODE -ne 0) {
                throw "Could not remove the incomplete Rank Hunter SageMath WSL environment."
            }
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $distroPath
        }
        else {
            throw "The WSL distribution '$ScienceDistroName' already exists outside Rank Hunter's managed data directory and did not pass the SageMath check. Setup will not overwrite it."
        }
    }

    Download-ScienceImage $zipPath

    Write-Step "Preparing SageMath scientific environment" "Extracting the downloaded WSL image." 58
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $extractPath
    New-Item -ItemType Directory -Force -Path $extractPath | Out-Null
    Expand-Archive -Path $zipPath -DestinationPath $extractPath -Force

    $tarPath = Join-Path $extractPath $ScienceTarName
    if (-not (Test-Path $tarPath)) {
        throw "The SageMath archive did not contain the expected WSL image: $ScienceTarName"
    }

    Write-Step "Installing SageMath scientific environment" "Importing the ready-made Linux/SageMath image into WSL2." 68
    New-Item -ItemType Directory -Force -Path $distroPath | Out-Null
    & wsl.exe --import $ScienceDistroName $distroPath $tarPath --version 2
    if ($LASTEXITCODE -ne 0) {
        throw "WSL could not import the SageMath scientific environment (exit $LASTEXITCODE)."
    }

    $science = Find-ScienceEnvironment
    if (-not $science -or $science.Distro -ne $ScienceDistroName) {
        throw "The imported SageMath environment did not pass its post-install verification."
    }

    Remove-Item -Force -ErrorAction SilentlyContinue $zipPath
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $extractPath
    return $science
}

function Convert-ToWslPath(
    [string]$WindowsPath,
    [string]$Distro,
    [string]$User = "root"
) {
    $raw = & wsl.exe -d $Distro -u $User -- wslpath -a $WindowsPath
    if ($LASTEXITCODE -ne 0) {
        throw "Could not convert Windows path for WSL: $WindowsPath"
    }
    return (($raw | Select-Object -Last 1) -replace [char]0, "").Trim()
}

function Invoke-WslScript(
    [string]$Distro,
    [string]$User,
    [string]$WindowsScript,
    [string[]]$Arguments
) {
    $wslScript = Convert-ToWslPath -WindowsPath $WindowsScript -Distro $Distro -User $User
    $args = @("-d", $Distro, "-u", $User, "--", "bash", $wslScript)
    $args += $Arguments

    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & wsl.exe @args 2>&1 | ForEach-Object {
            $line = $_.ToString()
            Add-Content -Encoding UTF8 -Path $LogPath -Value $line
            if (-not $Quiet) {
                Write-Host $line
            }
        }
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previous
    }

    if ($exitCode -ne 0) {
        throw "WSL setup step failed: $([IO.Path]::GetFileName($WindowsScript)) (exit $exitCode)"
    }
}

function Remove-RankHunterShortcuts {
    $programs = [Environment]::GetFolderPath("Programs")

    foreach ($legacy in @(
        "Rank Hunter.lnk",
        "Rank Hunter Setup and Repair.lnk",
        "Stop Rank Hunter.lnk"
    )) {
        Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $programs $legacy)
    }

    $folder = Join-Path $programs "Rank Hunter"
    if (Test-Path $folder) {
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $folder
    }
}

function Install-RankHunterShortcut {
    $programs = [Environment]::GetFolderPath("Programs")
    $folder = Join-Path $programs "Rank Hunter"
    New-Item -ItemType Directory -Force -Path $folder | Out-Null

    $shortcutPath = Join-Path $folder "Rank Hunter.lnk"
    $powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    $launcher = Join-Path $ScriptRoot "RankHunterLauncher.ps1"

    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $powershell
    $shortcut.Arguments = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $launcher + '"'
    $shortcut.WorkingDirectory = $ScriptRoot
    $shortcut.Description = "Launch Rank Hunter"
    $icon = Join-Path $ScriptRoot "RankHunter.ico"
    if (Test-Path $icon) {
        $shortcut.IconLocation = "$icon,0"
    }
    $shortcut.Save()
}

function Resolve-RepoDirName(
    [string]$Distro,
    [string]$User,
    [string]$PreferredName
) {
    $homeProbe = Invoke-WslProbe -Distro $Distro -User $User -Command @("printenv", "HOME")
    if ($homeProbe.ExitCode -ne 0) {
        throw "Could not determine the Linux home directory for $User in $Distro."
    }

    $linuxHome = (($homeProbe.Output | Select-Object -Last 1) -replace [char]0, "").Trim()
    if (-not $linuxHome) {
        throw "Could not determine the Linux home directory for $User in $Distro."
    }

    for ($suffix = 1; $suffix -le 999; $suffix++) {
        $candidateName = if ($suffix -eq 1) { $PreferredName } else { "$PreferredName$suffix" }
        $candidatePath = "$linuxHome/$candidateName"

        $exists = Invoke-WslProbe -Distro $Distro -User $User -Command @("test", "-e", $candidatePath)
        if ($exists.ExitCode -ne 0) {
            if ($candidateName -ne $PreferredName) {
                Write-SetupLog "[Rank Hunter] '$PreferredName' is occupied; using '$candidateName' instead."
            }
            return $candidateName
        }

        $gitExists = Invoke-WslProbe -Distro $Distro -User $User -Command @("test", "-e", "$candidatePath/.git")
        if ($gitExists.ExitCode -eq 0) {
            $remoteProbe = Invoke-WslProbe -Distro $Distro -User $User -Command @(
                "git", "-C", $candidatePath, "remote", "get-url", "origin"
            )
            if ($remoteProbe.ExitCode -eq 0) {
                $remote = (($remoteProbe.Output | Select-Object -Last 1) -replace [char]0, "").Trim()
                if ($remote -eq $RepoUrl) {
                    if ($candidateName -ne $PreferredName) {
                        Write-SetupLog "[Rank Hunter] Reusing existing Rank Hunter checkout '$candidateName'."
                    }
                    return $candidateName
                }
            }
        }
    }

    throw "Could not find an available Rank Hunter directory name after checking $PreferredName through $PreferredName999."
}

function Test-RepoInstalled([string]$Distro, [string]$User) {
    $homeProbe = Invoke-WslProbe -Distro $Distro -User $User -Command @("printenv", "HOME")
    if ($homeProbe.ExitCode -ne 0) { return $false }

    $linuxHome = (($homeProbe.Output | Select-Object -Last 1) -replace [char]0, "").Trim()
    if (-not $linuxHome) { return $false }

    $launcher = "$linuxHome/$RepoDirName/scripts/run-ui.sh"
    $probe = Invoke-WslProbe -Distro $Distro -User $User -Command @("test", "-x", $launcher)
    return $probe.ExitCode -eq 0
}

try {
    Remove-RankHunterShortcuts

    if ($RepoDirName -notmatch '^[A-Za-z0-9._-]+$') {
        throw "RepoDirName contains unsupported characters: $RepoDirName"
    }

    Write-Step "Checking installed environments" "Detecting WSL2, SageMath, and Rank Hunter." 3

    if (-not (Test-WslReady)) {
        if ($InstallScience -or $InstallRankHunter) {
            Install-WslPlatform
        }
        elseif ($InstallWsl) {
            Install-WslPlatform
        }
        else {
            Save-State "nothing-selected" "No requested component needed installation."
            Write-SetupProgress "Nothing to install" "No environment component was selected." 100
            exit 0
        }
    }

    $science = $null
    if (Test-WslReady) {
        $science = Find-ScienceEnvironment
    }

    if (-not $science -and ($InstallScience -or $InstallRankHunter)) {
        $science = Import-ScienceEnvironment
    }

    if ($science) {
        Write-Step "Scientific environment ready" ("Using SageMath {0} in WSL distribution '{1}' as user '{2}'." -f $ScienceVersion, $science.Distro, $science.User) 75
    }

    if (-not $InstallRankHunter) {
        if ($science) {
            Save-State "environment-ready" "Requested Windows/Linux scientific environment is ready." $science.Distro $science.User $science.Python
        }
        else {
            Save-State "environment-ready" "Requested WSL2 environment is ready."
        }
        Write-SetupProgress "Environment ready" "Requested environment components completed successfully." 100
        exit 0
    }

    if (-not $science) {
        throw "Rank Hunter requires SageMath, but no usable SageMath environment is installed and SageMath installation was unchecked."
    }

    Write-Step "Preparing Rank Hunter prerequisites" "Installing only the Linux packages Rank Hunter needs." 80
    $prereqs = Join-Path $ScriptRoot "bootstrap-prereqs.sh"
    Invoke-WslScript -Distro $science.Distro -User "root" -WindowsScript $prereqs -Arguments @()

    $sourceWindowsPath = ""
    if ($SourceBundle) {
        if ([IO.Path]::IsPathRooted($SourceBundle)) {
            $sourceWindowsPath = $SourceBundle
        }
        else {
            $sourceWindowsPath = Join-Path $ScriptRoot $SourceBundle
        }
        if (-not (Test-Path $sourceWindowsPath)) {
            throw "Bundled Rank Hunter source is missing: $sourceWindowsPath"
        }
    }

    $resolvedRepoDirName = Resolve-RepoDirName -Distro $science.Distro -User $science.User -PreferredName $RepoDirName
    if ($resolvedRepoDirName -ne $RepoDirName) {
        Write-Step "Choosing Rank Hunter folder" "The requested folder '$RepoDirName' is occupied; using '$resolvedRepoDirName' instead." 84
        $RepoDirName = $resolvedRepoDirName
    }

    Write-Step "Installing Rank Hunter" "Installing the application into the verified SageMath environment." 88
    $userBootstrap = Join-Path $ScriptRoot "bootstrap-user.sh"
    $userArgs = @(
        "--repo-url", $RepoUrl,
        "--repo-ref", $RepoRef,
        "--repo-dir", $RepoDirName,
        "--science-python", $science.Python
    )

    if ($sourceWindowsPath) {
        $sourceWslPath = Convert-ToWslPath -WindowsPath $sourceWindowsPath -Distro $science.Distro -User $science.User
        $userArgs += @("--source-bundle", $sourceWslPath)
    }
    if ($SourceCommit) {
        $userArgs += @("--source-commit", $SourceCommit)
    }

    Invoke-WslScript -Distro $science.Distro -User $science.User -WindowsScript $userBootstrap -Arguments $userArgs

    Write-Step "Verifying Rank Hunter" "Checking the application before creating Windows shortcuts." 97
    if (-not (Test-RepoInstalled -Distro $science.Distro -User $science.User)) {
        throw "Rank Hunter installation finished without a runnable scripts/run-ui.sh."
    }

    Save-State "ready" "Rank Hunter is installed and ready." $science.Distro $science.User $science.Python
    Install-RankHunterShortcut
    Write-SetupProgress "Rank Hunter is ready" "Installation completed successfully. The Rank Hunter Start-menu shortcut is now available." 100

    if (-not $Quiet) {
        Write-Host ""
        Write-Host "[Rank Hunter] Installation complete." -ForegroundColor Green
    }
}
catch {
    Save-State "failed" $_.Exception.Message
    Write-SetupProgress "Setup needs attention" $_.Exception.Message 100
    Write-SetupLog "[Rank Hunter] Setup failed: $($_.Exception.Message)"
    if (-not $Quiet) {
        Write-Host ""
        Write-Host "[Rank Hunter] Setup failed: $($_.Exception.Message)" -ForegroundColor Red
    }
    exit 1
}
