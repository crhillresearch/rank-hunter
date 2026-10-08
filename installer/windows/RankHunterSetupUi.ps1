[CmdletBinding()]
param(
    [string]$RepoDirName = "rank-hunter",
    [switch]$CustomSelection,
    [switch]$InstallWsl,
    [switch]$InstallScience,
    [switch]$InstallRankHunter,
    [switch]$Repair
)

$ErrorActionPreference = "Stop"

$StateRoot = Join-Path $env:LOCALAPPDATA "RankHunter"
$StatePath = Join-Path $StateRoot "setup-state.json"
$ProgressPath = Join-Path $StateRoot ("setup-progress-{0}.json" -f [Guid]::NewGuid().ToString("N"))
$LogPath = Join-Path $StateRoot "setup.log"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$SetupScript = Join-Path $ScriptRoot "RankHunterSetup.ps1"
$NativeSystemDirectory = if (
    [Environment]::Is64BitOperatingSystem -and -not [Environment]::Is64BitProcess
) {
    Join-Path $env:SystemRoot "Sysnative"
}
else {
    Join-Path $env:SystemRoot "System32"
}
$PowerShellExe = Join-Path $NativeSystemDirectory "WindowsPowerShell\v1.0\powershell.exe"

New-Item -ItemType Directory -Force -Path $StateRoot | Out-Null
Remove-Item -Force -ErrorAction SilentlyContinue $ProgressPath
Set-Content -Path $LogPath -Encoding UTF8 -Value "[Rank Hunter] Setup started $([DateTime]::Now.ToString('s'))"

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$form = New-Object System.Windows.Forms.Form
$form.Text = "Rank Hunter Setup"
$form.ClientSize = New-Object System.Drawing.Size(560, 290)
$form.StartPosition = "CenterScreen"
$form.FormBorderStyle = "FixedDialog"
$form.MaximizeBox = $false
$form.MinimizeBox = $false
$form.ShowInTaskbar = $true
$iconPath = Join-Path $ScriptRoot "RankHunter.ico"
if (Test-Path $iconPath) {
    try { $form.Icon = New-Object System.Drawing.Icon($iconPath) } catch {}
}

$title = New-Object System.Windows.Forms.Label
$title.Text = "Setting up Rank Hunter"
$title.Font = New-Object System.Drawing.Font("Segoe UI", 15, [System.Drawing.FontStyle]::Bold)
$title.AutoSize = $true
$title.Location = New-Object System.Drawing.Point(24, 20)
$form.Controls.Add($title)

$stage = New-Object System.Windows.Forms.Label
$stage.Text = "Checking your system..."
$stage.Font = New-Object System.Drawing.Font("Segoe UI", 10)
$stage.AutoSize = $false
$stage.Size = New-Object System.Drawing.Size(510, 28)
$stage.Location = New-Object System.Drawing.Point(25, 62)
$form.Controls.Add($stage)

$progress = New-Object System.Windows.Forms.ProgressBar
$progress.Style = "Continuous"
$progress.MarqueeAnimationSpeed = 0
$progress.Minimum = 0
$progress.Maximum = 100
$progress.Value = 1
$progress.Size = New-Object System.Drawing.Size(510, 22)
$progress.Location = New-Object System.Drawing.Point(25, 94)
$form.Controls.Add($progress)

$detail = New-Object System.Windows.Forms.Label
$detail.Text = "Existing compatible environments are detected and reused automatically."
$detail.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$detail.AutoSize = $false
$detail.Size = New-Object System.Drawing.Size(510, 40)
$detail.Location = New-Object System.Drawing.Point(25, 128)
$form.Controls.Add($detail)

$note = New-Object System.Windows.Forms.Label
$note.Text = "The scientific engine is installed once and reused by future Rank Hunter updates."
$note.Font = New-Object System.Drawing.Font("Segoe UI", 8)
$note.ForeColor = [System.Drawing.SystemColors]::GrayText
$note.AutoSize = $false
$note.Size = New-Object System.Drawing.Size(510, 34)
$note.Location = New-Object System.Drawing.Point(25, 166)
$form.Controls.Add($note)

$heartbeat = New-Object System.Windows.Forms.Label
$heartbeat.Text = "Working...  elapsed 00:00"
$heartbeat.Font = New-Object System.Drawing.Font("Segoe UI", 8, [System.Drawing.FontStyle]::Bold)
$heartbeat.AutoSize = $false
$heartbeat.Size = New-Object System.Drawing.Size(290, 24)
$heartbeat.Location = New-Object System.Drawing.Point(25, 208)
$form.Controls.Add($heartbeat)

$detailsButton = New-Object System.Windows.Forms.Button
$detailsButton.Text = "Show details"
$detailsButton.Size = New-Object System.Drawing.Size(105, 30)
$detailsButton.Location = New-Object System.Drawing.Point(326, 243)
$detailsButton.Add_Click({
    if (Test-Path $LogPath) {
        Start-Process notepad.exe -ArgumentList @($LogPath)
    }
})
$form.Controls.Add($detailsButton)

$closeButton = New-Object System.Windows.Forms.Button
$closeButton.Text = "Cancel"
$closeButton.Size = New-Object System.Drawing.Size(105, 30)
$closeButton.Location = New-Object System.Drawing.Point(438, 243)
$form.Controls.Add($closeButton)

$arguments = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", ('"' + $SetupScript + '"'),
    "-RepoDirName", $RepoDirName,
    "-Quiet",
    "-ProgressPath", ('"' + $ProgressPath + '"'),
    "-LogPath", ('"' + $LogPath + '"')
)
if ($CustomSelection) {
    $arguments += "-CustomSelection"
    if ($InstallWsl) { $arguments += "-InstallWsl" }
    if ($InstallScience) { $arguments += "-InstallScience" }
    if ($InstallRankHunter) { $arguments += "-InstallRankHunter" }
}
if ($Repair) { $arguments += "-Repair" }

$process = Start-Process -FilePath $PowerShellExe -ArgumentList $arguments -WindowStyle Hidden -PassThru
$startedAt = Get-Date

$finished = $false
$cancelRequested = $false

$closeButton.Add_Click({
    if (-not $finished) {
        $answer = [System.Windows.Forms.MessageBox]::Show(
            "Stop Rank Hunter setup? You can safely rerun the Rank Hunter installer later to continue.",
            "Cancel Rank Hunter Setup",
            [System.Windows.Forms.MessageBoxButtons]::YesNo,
            [System.Windows.Forms.MessageBoxIcon]::Question
        )
        if ($answer -eq [System.Windows.Forms.DialogResult]::Yes) {
            $script:cancelRequested = $true
            try {
                Start-Process -FilePath "taskkill.exe" -ArgumentList @("/PID", $process.Id, "/T", "/F") -WindowStyle Hidden -Wait | Out-Null
            }
            catch {
                try { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue } catch {}
            }
            $form.Close()
        }
    }
    else {
        $form.Close()
    }
})

$form.Add_FormClosing({
    param($sender, $e)
    if (-not $finished -and -not $cancelRequested) {
        $e.Cancel = $true
        $closeButton.PerformClick()
    }
})

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 500
$timer.Add_Tick({
    if (-not $finished) {
        $elapsed = (Get-Date) - $startedAt
        $dots = "." * (([int][Math]::Floor($elapsed.TotalSeconds) % 3) + 1)
        $heartbeat.Text = ("Working{0}  elapsed {1:00}:{2:00}" -f $dots, [Math]::Floor($elapsed.TotalMinutes), $elapsed.Seconds)
    }

    if (Test-Path $ProgressPath) {
        try {
            $status = Get-Content -Raw $ProgressPath | ConvertFrom-Json
            if ($status.stage) { $stage.Text = [string]$status.stage }
            if ($status.detail) { $detail.Text = [string]$status.detail }
            if ($null -ne $status.percent -and [int]$status.percent -ge 0) {
                if ($progress.Style -ne "Continuous") {
                    $progress.Style = "Continuous"
                    $progress.MarqueeAnimationSpeed = 0
                }
                $progress.Value = [Math]::Max(0, [Math]::Min(100, [int]$status.percent))
            }
            elseif ($progress.Style -ne "Continuous") {
                $progress.Style = "Continuous"
                $progress.MarqueeAnimationSpeed = 0
            }
        }
        catch {
            # A writer may be replacing the small JSON file while we poll it.
        }
    }

    $process.Refresh()
    if ($process.HasExited -and -not $finished) {
        $script:finished = $true
        $timer.Stop()
        $progress.Style = "Continuous"
        $progress.MarqueeAnimationSpeed = 0
        $heartbeat.Text = "Finished"

        $state = $null
        if (Test-Path $StatePath) {
            try { $state = Get-Content -Raw $StatePath | ConvertFrom-Json } catch {}
        }

        if ($process.ExitCode -eq 0 -and $state -and $state.status -eq "ready") {
            $progress.Value = 100
            $stage.Text = "Rank Hunter is ready"
            $detail.Text = "Setup completed successfully. You can launch Rank Hunter from the Start menu."
            $note.Text = "The Start-menu shortcut was created only after the environment and app passed verification."
            $closeButton.Text = "Close"
        }
        elseif ($process.ExitCode -eq 0 -and $state -and $state.status -eq "environment-ready") {
            $progress.Value = 100
            $stage.Text = "Environment setup complete"
            $detail.Text = [string]$state.detail
            $note.Text = "No Rank Hunter shortcuts were created because application installation was skipped."
            $closeButton.Text = "Close"
        }
        elseif ($process.ExitCode -eq 0 -and $state -and $state.status -eq "nothing-selected") {
            $progress.Value = 100
            $stage.Text = "Nothing to install"
            $detail.Text = [string]$state.detail
            $note.Text = ""
            $closeButton.Text = "Close"
        }
        elseif ($process.ExitCode -eq 0 -and $state -and $state.status -eq "reboot-required") {
            $progress.Value = 100
            $stage.Text = "Windows restart required"
            $detail.Text = [string]$state.detail
            $note.Text = "After restarting Windows, rerun the Rank Hunter installer to continue."
            $closeButton.Text = "Close"
        }
        else {
            $progress.Value = 100
            $stage.Text = "Setup needs attention"
            if ($state -and $state.detail) {
                $detail.Text = [string]$state.detail
            }
            else {
                $detail.Text = "Rank Hunter setup stopped before completion."
            }
            $note.Text = "Click Show details for the setup log. Rerunning the installer is safe."
            $closeButton.Text = "Close"
        }
    }
})
$timer.Start()

[void]$form.ShowDialog()
$timer.Dispose()

Remove-Item -Force -ErrorAction SilentlyContinue $ProgressPath

if ($cancelRequested) {
    exit 2
}

$process.Refresh()
exit $process.ExitCode
