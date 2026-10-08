[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$OutputPath,
    [string]$RepoDirName = "rank-hunter"
)

$ErrorActionPreference = "SilentlyContinue"

function Clean-Line($Value) {
    if ($null -eq $Value) { return "" }
    return ((($Value | Select-Object -Last 1) -replace [char]0, "").Trim())
}

function Get-WslDistros {
    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
        return @()
    }
    $raw = & wsl.exe --list --quiet 2>$null
    if ($LASTEXITCODE -ne 0) { return @() }
    return @(
        $raw |
            ForEach-Object { Clean-Line $_ } |
            Where-Object { $_ }
    )
}

function Test-WslUser([string]$Distro, [string]$User) {
    & wsl.exe -d $Distro -u $User -- id -u 1>$null 2>$null
    return $LASTEXITCODE -eq 0
}

function Resolve-WslUser([string]$Distro) {
    $defaultUser = Clean-Line (& wsl.exe -d $Distro -- id -un 2>$null)
    if ($LASTEXITCODE -eq 0 -and $defaultUser -and $defaultUser -ne "root") {
        return $defaultUser
    }

    foreach ($candidate in @("user", "sage")) {
        if (Test-WslUser -Distro $Distro -User $candidate) {
            return $candidate
        }
    }

    $passwd = Clean-Line (& wsl.exe -d $Distro -u root -- getent passwd 1000 2>$null)
    if ($LASTEXITCODE -eq 0 -and $passwd) {
        $parts = $passwd.Split(":")
        if ($parts.Count -ge 1 -and $parts[0] -and $parts[0] -ne "root") {
            return $parts[0]
        }
    }

    return ""
}

function Test-WslExecutable([string]$Distro, [string]$User, [string]$Path) {
    & wsl.exe -d $Distro -u $User -- test -x $Path 1>$null 2>$null
    return $LASTEXITCODE -eq 0
}

function Resolve-SciencePython([string]$Distro, [string]$User) {
    $linuxHome = Clean-Line (& wsl.exe -d $Distro -u $User -- printenv HOME 2>$null)

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

    foreach ($candidate in $candidates) {
        if (-not (Test-WslExecutable -Distro $Distro -User $User -Path $candidate)) {
            continue
        }

        $result = & wsl.exe -d $Distro -u $User -- $candidate -c "import sage.all,sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0) {
            $path = Clean-Line $result
            if ($path) { return $path }
        }
    }

    foreach ($sage in @("/usr/bin/sage", "/usr/local/bin/sage")) {
        if (-not (Test-WslExecutable -Distro $Distro -User $User -Path $sage)) {
            continue
        }

        $result = & wsl.exe -d $Distro -u $User -- $sage -python -c "import sage.all,sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0) {
            $path = Clean-Line $result
            if ($path) { return $path }
        }
    }

    $sagePath = Clean-Line (& wsl.exe -d $Distro -u $User -- which sage 2>$null)
    if ($LASTEXITCODE -eq 0 -and $sagePath) {
        $result = & wsl.exe -d $Distro -u $User -- $sagePath -python -c "import sage.all,sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0) {
            $path = Clean-Line $result
            if ($path) { return $path }
        }
    }

    return ""
}

function Test-RankHunter([string]$Distro, [string]$User, [string]$RepoDir) {
    $linuxHome = Clean-Line (& wsl.exe -d $Distro -u $User -- printenv HOME 2>$null)
    if (-not $linuxHome) { return $false }
    & wsl.exe -d $Distro -u $User -- test -x "$linuxHome/$RepoDir/scripts/run-ui.sh" 1>$null 2>$null
    return $LASTEXITCODE -eq 0
}

$wslExe = Get-Command wsl.exe -ErrorAction SilentlyContinue
$distros = @(Get-WslDistros)
$wslReady = $false
if ($wslExe) {
    & wsl.exe --status 1>$null 2>$null
    $wslReady = ($LASTEXITCODE -eq 0) -or ($distros.Count -gt 0)
}

$bestDistro = ""
$bestUser = ""
$bestScience = ""
$bestRank = $false
$bestScore = -1

foreach ($distro in $distros) {
    $user = Resolve-WslUser -Distro $distro
    if (-not $user) { continue }

    $science = Resolve-SciencePython -Distro $distro -User $user
    $rankReady = Test-RankHunter -Distro $distro -User $user -RepoDir $RepoDirName

    $score = 0
    if ($science) { $score += 10 }
    if ($rankReady) { $score += 20 }
    if ($distro -like "RankHunter-Sage*") { $score += 4 }
    elseif ($distro -like "SageMath-*") { $score += 3 }
    elseif ($distro -like "Ubuntu*") { $score += 2 }

    if ($score -gt $bestScore) {
        $bestScore = $score
        $bestDistro = $distro
        $bestUser = $user
        $bestScience = $science
        $bestRank = $rankReady
    }
}

$lines = @(
    "[preflight]",
    "wsl_ready=$([int]$wslReady)",
    "distro_count=$($distros.Count)",
    "distro=$bestDistro",
    "distro_user=$bestUser",
    "science_ready=$([int][bool]$bestScience)",
    "science_python=$bestScience",
    "rank_hunter_ready=$([int]$bestRank)"
)

$parent = Split-Path -Parent $OutputPath
if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
[IO.File]::WriteAllLines($OutputPath, $lines, [Text.UTF8Encoding]::new($false))
