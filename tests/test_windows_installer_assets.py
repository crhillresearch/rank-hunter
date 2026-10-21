from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
WIN = ROOT / "installer" / "windows"


def read(name: str) -> str:
    return (WIN / name).read_text(encoding="utf-8")


def test_windows_installer_package_is_complete() -> None:
    required = {
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
        "build.ps1",
        "validate.ps1",
        "README.md",
        "build-source.json",
    }
    assert required <= {path.name for path in WIN.iterdir() if path.is_file()}


def test_installer_has_environment_checkboxes_with_times() -> None:
    package = read("RankHunter.iss")

    assert "Choose Rank Hunter environments" in package
    assert "Uncheck anything you do not want installed" in package
    assert "WSL 2 / Linux backend" in package
    assert "about 2-10 min if missing" in package
    assert "SageMath scientific engine" in package
    assert "about 5-15 min; ~1.4 GB download if missing" in package
    assert "Rank Hunter application" in package
    assert "about 3-10 min" in package
    assert "WslCheck.Checked" in package
    assert "ScienceCheck.Checked" in package
    assert "AppCheck.Checked" in package


def test_windows_installer_branding_and_finish_launch_contract() -> None:
    package = read("RankHunter.iss")
    setup = read("RankHunterSetup.ps1")
    setup_ui = read("RankHunterSetupUi.ps1")
    build = read("build.ps1")
    icon_svg = read("RankHunterIcon.svg")
    icon_builder = read("make-icon.ps1")

    assert '#define RankHunterPublisher "C. R. Hill"' in package
    assert "AppPublisher={#RankHunterPublisher}" in package
    assert "VersionInfoCompany={#RankHunterPublisher}" in package
    assert "SetupIconFile=RankHunter.ico" in package
    assert "UninstallDisplayIcon={app}\\RankHunter.ico" in package
    assert 'Source: "RankHunter.ico"; DestDir: "{app}"; Flags: ignoreversion' in package

    assert 'Description: "Run Rank Hunter on close"' in package
    assert 'Filename: "{autoprograms}\\Rank Hunter\\Rank Hunter.lnk"' in package
    assert "Flags: postinstall nowait skipifsilent shellexec" in package
    assert "Check: ShouldOfferRunRankHunter" in package
    assert "function ShouldOfferRunRankHunter: Boolean;" in package
    assert "FileExists(ExpandConstant('{localappdata}\\RankHunter\\setup-shortcut-ready'))" in package
    assert "FileExists(ExpandConstant('{autoprograms}\\Rank Hunter\\Rank Hunter.lnk'))" in package
    assert '$ReadyMarkerPath = Join-Path $StateRoot "setup-shortcut-ready"' in setup
    assert "Remove-Item -Force -ErrorAction SilentlyContinue $ReadyMarkerPath" in setup
    assert 'Test-Path -LiteralPath $shortcutPath -PathType Leaf' in setup
    assert 'Set-Content -Encoding ASCII -Path $ReadyMarkerPath -Value "ready"' in setup
    assert setup.index("Install-RankHunterShortcut\n    $shortcutPath") < setup.index('Save-State "ready" "Rank Hunter is installed and ready."')
    assert "Result := AppCheck.Checked" in package

    assert 'fill="#1E8BFF"' in icon_svg
    assert 'fill="#FFFFFF"' in icon_svg
    assert 'stroke="#FFFFFF"' in icon_svg
    assert "<rect " in icon_svg
    assert "canonical Rank Hunter SVG mark" in icon_builder
    assert "@(16, 24, 32, 48, 64, 128, 256)" in icon_builder
    assert '[IO.File]::WriteAllBytes($OutputPath' in icon_builder
    assert '& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $IconBuildScript' in build

    assert '$shortcut.IconLocation = "$icon,0"' in setup
    assert '$iconPath = Join-Path $ScriptRoot "RankHunter.ico"' in setup_ui
    assert '$form.Icon = New-Object System.Drawing.Icon($iconPath)' in setup_ui


def test_setup_progress_advances_through_real_install_stages() -> None:
    setup = read("RankHunterSetup.ps1")
    ui = read("RankHunterSetupUi.ps1")

    assert "[int]$Percent = -1" in setup
    assert "Write-SetupProgress -Stage $Message -Detail $Detail -Percent $Percent" in setup
    assert 'Write-Step "Checking installed environments" "Detecting WSL2, SageMath, and Rank Hunter." 3' in setup
    assert 'Write-Step "Downloading SageMath scientific environment"' in setup
    assert " 15" in setup[setup.index('Write-Step "Downloading SageMath scientific environment"'):setup.index("Import-Module BitsTransfer")]
    assert '$overallPercent = 15 + [Math]::Floor(40 * $downloadPercent / 100)' in setup
    assert 'Write-Step "Preparing SageMath scientific environment" "Extracting the downloaded WSL image." 58' in setup
    assert 'Write-Step "Installing SageMath scientific environment" "Importing the ready-made Linux/SageMath image into WSL2." 68' in setup
    assert 'Write-Step "Preparing Rank Hunter prerequisites" "Installing only the Linux packages Rank Hunter needs." 80' in setup
    assert 'Write-Step "Installing Rank Hunter" "Installing the application into the verified SageMath environment." 88' in setup
    assert 'Write-Step "Verifying Rank Hunter" "Checking the application before creating Windows shortcuts." 97' in setup
    assert 'Write-SetupProgress "Rank Hunter is ready"' in setup

    assert '$progress.Style = "Continuous"' in ui
    assert "$progress.Minimum = 0" in ui
    assert "$progress.Maximum = 100" in ui
    assert "$progress.Value = 1" in ui
    assert '[int]$status.percent' in ui


def test_preflight_detects_and_reuses_existing_environments() -> None:
    package = read("RankHunter.iss")
    preflight = read("RankHunterPreflight.ps1")

    assert 'Source: "RankHunterPreflight.ps1"; Flags: dontcopy' in package
    assert "ApplyPreflight" in package
    assert "GetIniString('preflight', 'wsl_ready'" in package
    assert "GetIniString('preflight', 'science_ready'" in package
    assert "GetIniString('preflight', 'rank_hunter_ready'" in package
    assert "Existing Linux environment will be reused" in package
    assert "Detected: SageMath" in package

    assert "Get-WslDistros" in preflight
    assert "Resolve-WslUser" in preflight
    assert "Resolve-SciencePython" in preflight
    assert "Test-RankHunter" in preflight
    assert "/opt/rankhunter/miniforge3/envs/sage/bin/python" in preflight
    assert "$linuxHome/miniforge3/envs/sage/bin/python" in preflight
    assert "$linuxHome/mambaforge/envs/sage/bin/python" in preflight
    assert "$linuxHome/miniconda3/envs/sage/bin/python" in preflight
    assert "/usr/bin/sage" in preflight
    assert "import sage.all,sys; print(sys.executable)" in preflight


def test_environment_dependencies_are_validated_before_install() -> None:
    package = read("RankHunter.iss")

    assert "WSL 2 is required for SageMath and Rank Hunter" in package
    assert "Rank Hunter requires a SageMath scientific environment" in package
    assert "Nothing is selected for installation." in package


def test_official_sagemath_wsl_image_is_pinned() -> None:
    source = json.loads(read("build-source.json"))
    build = read("build.ps1")

    assert source["sage_wsl_version"] == "10.10"
    assert source["sage_wsl_distro"] == "RankHunter-Sage-10.10"
    assert source["sage_wsl_url"] == (
        "https://github.com/sagemath/sage-binder-env/releases/download/"
        "v10.10/sagemath-10.10-wsl.zip"
    )
    assert source["sage_wsl_sha256"] == (
        "87d2f2542a48ab0bee07defcad196335576b3c1b621233f5461a9aedd44edff6"
    )
    assert source["sage_wsl_size_bytes"] == 1376792539
    assert source["sage_wsl_tar_name"] == "sagemath-10.10-wsl.tar"

    assert '[string]$SageWslVersion = "10.10"' in build
    assert "1376792539" in build
    assert "sagemath-10.10-wsl.zip" in build


def test_setup_downloads_and_imports_science_environment_itself() -> None:
    setup = read("RankHunterSetup.ps1")

    assert "Start-BitsTransfer" in setup
    assert "Get-BitsTransfer" in setup
    assert "BytesTransferred" in setup
    assert "Write-SetupProgress" in setup
    assert "Get-FileHash -Algorithm SHA256" in setup
    assert "Expand-Archive" in setup
    assert "& wsl.exe --import $ScienceDistroName" in setup
    assert "--version 2" in setup
    assert "Downloading SageMath scientific environment" in setup
    assert "Installing SageMath scientific environment" in setup




def test_setup_worker_has_no_duplicate_or_stale_tail() -> None:
    setup = read("RankHunterSetup.ps1")

    assert setup.count("function Get-SciencePython") == 1
    assert setup.count("function Find-ScienceEnvironment") == 1
    assert setup.count("function Install-RankHunterShortcut") == 1
    assert setup.count('Write-Step "Verifying Rank Hunter"') == 1
    assert "$probe = @'" not in setup
    assert "sh -lc $probe" not in setup


def test_setup_reuses_existing_sage_path_before_downloading() -> None:
    setup = read("RankHunterSetup.ps1")

    assert "function Find-ScienceEnvironment" in setup
    assert "function Get-SciencePython" in setup
    assert "/opt/rankhunter/miniforge3/envs/sage/bin/python" in setup
    assert "$linuxHome/miniforge3/envs/sage/bin/python" in setup
    assert "$linuxHome/mambaforge/envs/sage/bin/python" in setup
    assert "$linuxHome/miniconda3/envs/sage/bin/python" in setup
    assert "/usr/bin/sage" in setup
    assert 'Write-Step "Scientific environment ready"' in setup
    assert '"--science-python", $science.Python' in setup


def test_science_detection_skips_missing_candidates_without_shell_errors() -> None:
    setup = read("RankHunterSetup.ps1")

    start = setup.index("function Get-SciencePython")
    end = setup.index("function Find-ScienceEnvironment")
    science_probe = setup[start:end]

    assert "sh -lc" not in science_probe
    assert "@'" not in science_probe
    assert 'Invoke-WslProbe -Distro $Distro -User $User -Command @("test", "-x", $python)' in science_probe
    assert 'Invoke-WslProbe -Distro $Distro -User $User -Command @("test", "-x", $sage)' in science_probe
    assert '"import sage.all,sys; print(sys.executable)"' in science_probe
    assert 'Invoke-WslProbe -Distro $Distro -User "root" -Command @("getent", "passwd", "1000")' in setup


def test_preflight_checks_user_local_sage_before_claiming_it_is_missing() -> None:
    preflight = read("RankHunterPreflight.ps1")

    assert 'function Test-WslExecutable' in preflight
    assert '$linuxHome/miniforge3/envs/sage/bin/python' in preflight
    assert '$linuxHome/mambaforge/envs/sage/bin/python' in preflight
    assert '$linuxHome/miniconda3/envs/sage/bin/python' in preflight
    assert 'if (-not (Test-WslExecutable -Distro $Distro -User $User -Path $candidate))' in preflight
    assert 'bash -lc "command -v sage || true"' not in preflight


def test_powershell_home_constant_is_never_shadowed() -> None:
    setup = read("RankHunterSetup.ps1")
    preflight = read("RankHunterPreflight.ps1")
    launcher = read("RankHunterLauncher.ps1")

    assert "$home =" not in setup.lower()
    assert "$home =" not in preflight.lower()
    assert "$home =" not in launcher.lower()
    assert "$linuxhome =" in setup.lower()
    assert "$linuxhome =" in preflight.lower()
    assert "$linuxhome =" in launcher.lower()


def test_wsl_install_is_optional_and_resumable() -> None:
    setup = read("RankHunterSetup.ps1")

    assert '"--install", "--no-distribution"' in setup
    assert "-Verb RunAs" in setup
    assert "reboot-required" in setup
    assert "Restart Windows, then run the Rank Hunter installer again." in setup
    assert "WSL2 is required but its installation was unchecked." in setup


def test_unchecked_options_are_not_installed() -> None:
    package = read("RankHunter.iss")
    setup = read("RankHunterSetup.ps1")
    ui = read("RankHunterSetupUi.ps1")

    assert " -CustomSelection" in package
    assert " -InstallWsl" in package
    assert " -InstallScience" in package
    assert " -InstallRankHunter" in package

    assert "[switch]$CustomSelection" in setup
    assert "[switch]$InstallWsl" in setup
    assert "[switch]$InstallScience" in setup
    assert "[switch]$InstallRankHunter" in setup
    assert 'Save-State "environment-ready"' in setup
    assert 'Save-State "nothing-selected"' in setup

    assert "[switch]$CustomSelection" in ui
    assert "[switch]$InstallRankHunter" in ui
    assert '$arguments += "-InstallRankHunter"' in ui


def test_occupied_rank_hunter_directory_gets_numbered_without_overwrite() -> None:
    setup = read("RankHunterSetup.ps1")
    user_bootstrap = read("bootstrap-user.sh")

    assert "function Resolve-RepoDirName" in setup
    assert '$candidateName = if ($suffix -eq 1) { $PreferredName } else { "$PreferredName$suffix" }' in setup
    assert 'Invoke-WslProbe -Distro $Distro -User $User -Command @("test", "-e", $candidatePath)' in setup
    assert '"git", "-C", $candidatePath, "remote", "get-url", "origin"' in setup
    assert 'if ($remote -eq $RepoUrl)' in setup
    assert 'using \'$resolvedRepoDirName\' instead' in setup
    assert '$RepoDirName = $resolvedRepoDirName' in setup

    assert "Removing empty existing target directory" not in user_bootstrap
    assert "resolved install path is occupied and is not a Git checkout" in user_bootstrap


def test_resolved_repo_directory_is_persisted_for_launcher() -> None:
    setup = read("RankHunterSetup.ps1")

    resolve_pos = setup.index("$resolvedRepoDirName = Resolve-RepoDirName")
    assign_pos = setup.index("$RepoDirName = $resolvedRepoDirName", resolve_pos)
    bootstrap_pos = setup.index('Invoke-WslScript -Distro $science.Distro -User $science.User', assign_pos)
    save_pos = setup.index('Save-State "ready"', bootstrap_pos)

    assert resolve_pos < assign_pos < bootstrap_pos < save_pos
    assert "repo_dir = $RepoDirName" in setup


def test_environment_setup_finishes_before_installer_completes() -> None:
    package = read("RankHunter.iss")

    setup_entry = next(
        line
        for line in package.splitlines()
        if 'Description: "Set up the selected Rank Hunter environments"' in line
    )
    assert "Flags: waituntilterminated" in setup_entry
    assert "postinstall" not in setup_entry

    launch_entry = next(
        line
        for line in package.splitlines()
        if 'Description: "Run Rank Hunter on close"' in line
    )
    assert "postinstall" in launch_entry


def test_only_one_start_menu_shortcut_is_created_after_verification() -> None:
    package = read("RankHunter.iss")
    setup = read("RankHunterSetup.ps1")

    assert "[Icons]" not in package
    assert "[InstallDelete]" in package
    assert 'Type: files; Name: "{autoprograms}\\Rank Hunter.lnk"' in package
    assert 'Type: files; Name: "{autoprograms}\\Rank Hunter Setup and Repair.lnk"' in package
    assert 'Type: files; Name: "{autoprograms}\\Stop Rank Hunter.lnk"' in package
    assert 'Type: filesandordirs; Name: "{autoprograms}\\Rank Hunter"' in package
    assert "function Remove-RankHunterShortcuts" in setup
    assert '"Rank Hunter.lnk"' in setup
    assert '"Rank Hunter Setup and Repair.lnk"' in setup
    assert '"Stop Rank Hunter.lnk"' in setup
    assert "function Install-RankHunterShortcut" in setup

    cleanup_pos = setup.index("Remove-RankHunterShortcuts", setup.index("try {"))
    check_pos = setup.index('Write-Step "Checking installed environments"')
    verify_pos = setup.index('Write-Step "Verifying Rank Hunter"')
    shortcut_pos = setup.index("Install-RankHunterShortcut", verify_pos)
    shortcut_check_pos = setup.index("Test-Path -LiteralPath $shortcutPath -PathType Leaf", shortcut_pos)
    state_pos = setup.index('Save-State "ready"', shortcut_check_pos)
    ready_marker_pos = setup.index('Set-Content -Encoding ASCII -Path $ReadyMarkerPath -Value "ready"', state_pos)
    assert cleanup_pos < check_pos < verify_pos < shortcut_pos < shortcut_check_pos < state_pos < ready_marker_pos

    assert '"Rank Hunter.lnk"' in setup

    install_start = setup.index("function Install-RankHunterShortcut")
    install_end = setup.index("function Test-RepoInstalled")
    install_block = setup[install_start:install_end]
    assert '"Rank Hunter.lnk"' in install_block
    assert '"Rank Hunter Setup and Repair.lnk"' not in install_block
    assert '"Stop Rank Hunter.lnk"' not in install_block


def test_setup_progress_file_cannot_abort_installation() -> None:
    setup = read("RankHunterSetup.ps1")
    ui = read("RankHunterSetupUi.ps1")

    assert "for ($attempt = 1; $attempt -le 10; $attempt++)" in setup
    assert "catch [System.IO.IOException]" in setup
    assert "catch [System.UnauthorizedAccessException]" in setup
    assert "Start-Sleep -Milliseconds 50" in setup
    assert "A busy" in setup
    assert "progress file must not turn successful environment/app work into failure" in setup

    assert '("setup-progress-{0}.json" -f [Guid]::NewGuid().ToString("N"))' in ui
    assert "Remove-Item -Force -ErrorAction SilentlyContinue $ProgressPath" in ui
    assert '"setup-progress.json"' not in ui


def test_setup_ui_has_real_progress_and_visible_heartbeat() -> None:
    ui = read("RankHunterSetupUi.ps1")

    assert "Working...  elapsed 00:00" in ui
    assert "$heartbeat.Text" in ui
    assert "status.percent" in ui
    assert "$progress.Value" in ui
    assert "taskkill.exe" in ui
    assert "Show details" in ui


def test_windows_paths_are_native_64_bit_and_wow64_safe() -> None:
    package = read("RankHunter.iss")
    launcher = read("RankHunterLauncher.ps1")
    setup_ui = read("RankHunterSetupUi.ps1")
    stop = read("RankHunterStop.ps1")

    assert "ArchitecturesAllowed=x64compatible" in package
    assert "ArchitecturesInstallIn64BitMode=x64compatible" in package

    finish_line = next(
        line
        for line in package.splitlines()
        if 'Description: "Run Rank Hunter on close"' in line
    )
    assert '"{autoprograms}\\Rank Hunter\\Rank Hunter.lnk"' in finish_line
    assert "shellexec" in finish_line
    assert "WindowsPowerShell" not in finish_line

    for source in (launcher, setup_ui, stop):
        assert "[Environment]::Is64BitOperatingSystem" in source
        assert "[Environment]::Is64BitProcess" in source
        assert 'Join-Path $env:SystemRoot "Sysnative"' in source
        assert 'Join-Path $env:SystemRoot "System32"' in source

    assert '$WslExe = Join-Path $NativeSystemDirectory "wsl.exe"' in launcher
    assert '$PowerShellExe = Join-Path $NativeSystemDirectory "WindowsPowerShell\\v1.0\\powershell.exe"' in launcher
    assert "& $WslExe -d $DistroName -u $DistroUser -- bash $wslLaunchScript" in launcher
    assert "Start-Process -FilePath $PowerShellExe" in setup_ui
    assert '$WslExe = Join-Path $NativeSystemDirectory "wsl.exe"' in stop
    assert "& wsl.exe" not in stop


def test_launcher_waits_for_health_gated_detached_wsl_start() -> None:
    launcher = read("RankHunterLauncher.ps1")
    linux_launcher = read("launch-rank-hunter.sh")

    assert "& $WslExe -d $DistroName -u $DistroUser -- bash $wslLaunchScript" in launcher
    assert "$launchExitCode = $LASTEXITCODE" in launcher
    assert "WSL reported the detached UI is listening." in launcher
    assert "Start-Process -FilePath $PowerShellExe" not in launcher
    assert "$uiHost.HasExited" not in launcher

    assert "nohup setsid env" in linux_launcher
    assert 'printf \'%s\\n\' "$ui_pid" >"$PID_FILE"' in linux_launcher
    assert '"/dev/tcp/127.0.0.1/$PORT"' in linux_launcher
    assert "for _ in $(seq 1 180)" in linux_launcher
    assert 'sleep 0.5' in linux_launcher
    assert ': >"$LOG_FILE"' in linux_launcher


def test_start_menu_shortcut_targets_64_bit_powershell() -> None:
    setup = read("RankHunterSetup.ps1")

    start = setup.index("function Install-RankHunterShortcut")
    end = setup.index("function Resolve-RepoDirName")
    shortcut_block = setup[start:end]

    assert '$env:SystemRoot "System32\\WindowsPowerShell\\v1.0\\powershell.exe"' in shortcut_block
    assert "$PSHOME" not in shortcut_block


def test_launcher_uses_persisted_environment_state() -> None:
    launcher = read("RankHunterLauncher.ps1")
    stop = read("RankHunterStop.ps1")
    setup = read("RankHunterSetup.ps1")

    assert "distro_user = $DistroUser" in setup
    assert "science_python = $SciencePython" in setup
    assert '$DistroName = [string]$state.distro' in launcher
    assert '$DistroUser = [string]$state.distro_user' in launcher
    assert "-u $DistroUser" in launcher
    assert "_stcore/health" in launcher
    assert "http://localhost:$Port" in launcher
    assert "Rerun the Rank Hunter installer to repair" in launcher
    assert '$DistroName = [string]$state.distro' in stop
    assert '$DistroUser = [string]$state.distro_user' in stop


def test_bundled_source_bootstrap_can_install_from_public_release_tag(tmp_path: Path) -> None:
    """CI bundle branches and public release tags differ; installation must work."""
    if os.geteuid() == 0:
        pytest.skip("Bootstrap deliberately rejects running as root")

    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(
        ["git", "init", "-b", "rank-hunter-release-source"], cwd=source, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    (source / "install.sh").write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\necho fixture-installed\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "install.sh"], cwd=source, check=True)
    subprocess.run(
        ["git", "-c", "user.name=RankHunterTest",
         "-c", "user.email=test@example.invalid", "commit", "-m", "Fixture"],
        cwd=source, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=source, text=True,
    ).strip()
    bundle = tmp_path / "release.bundle"
    subprocess.run(
        ["git", "bundle", "create", str(bundle),
         "refs/heads/rank-hunter-release-source"],
        cwd=source, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )

    science = tmp_path / "science-python"
    science.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    science.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    command = [
        "bash", str(WIN / "bootstrap-user.sh"),
        "--repo-url", "https://github.com/crhillresearch/rank-hunter.git",
        "--repo-ref", "v0.9.2",
        "--repo-dir", "rank-hunter",
        "--science-python", str(science),
        "--source-bundle", str(bundle),
        "--source-commit", sha,
    ]
    for _ in range(2):  # fresh install and idempotent update
        result = subprocess.run(
            command, cwd=ROOT, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env={**os.environ, "HOME": str(home)},
        )
        assert result.returncode == 0, (result.stdout, result.stderr)
        checkout = home / "rank-hunter"
        assert checkout.exists()
        assert subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=checkout, text=True,
        ).strip() == sha
        assert subprocess.check_output(
            ["git", "remote", "get-url", "origin"], cwd=checkout, text=True,
        ).strip() == "https://github.com/crhillresearch/rank-hunter.git"


def test_release_workflow_bundles_exact_source_with_public_origin() -> None:
    build = read("build.ps1")
    workflow = (ROOT / ".github" / "workflows" / "build-windows-installer.yml").read_text(
        encoding="utf-8"
    )
    package = read("RankHunter.iss")

    public_repo = "https://github.com/crhillresearch/rank-hunter.git"
    assert public_repo in workflow
    assert '$bundleBranch = "rank-hunter-release-source"' in workflow
    assert 'git bundle create $sourceBundle "refs/heads/$bundleBranch"' in workflow
    assert "git bundle verify $sourceBundle" in workflow
    assert "git clone --branch $bundleBranch $sourceBundle $smoke" in workflow
    assert "-RepoUrl $repoUrl" in workflow
    assert "-RepoRef $repoRef" in workflow
    assert "-SourceBundle $sourceBundle" in workflow
    assert "-SourceCommit $sourceCommit" in workflow
    assert '$repoRef = $env:GITHUB_REF_NAME' in workflow
    assert 'RankHunterSourceBundle' in package
    assert 'DestName: "rank-hunter.bundle"' in package

    assert "rank-hunter-dev" not in workflow
    assert "rank-hunter-dev" not in build
    assert "oct-release" not in workflow
    assert "[switch]$Development" not in build


def test_normal_installer_never_embeds_scientific_runtime() -> None:
    workflow = (ROOT / ".github" / "workflows" / "build-windows-installer.yml").read_text(
        encoding="utf-8"
    )
    package = read("RankHunter.iss")
    build = read("build.ps1")

    assert "build-runtime:" not in workflow
    assert "RankHunter-Scientific-Runtime" not in workflow
    assert "actions/download-artifact@v4" not in workflow
    assert "RankHunterRuntimeSource" not in package
    assert "scientific-runtime.tar.zst" not in package
    assert "$installer.Length -gt 100MB" in workflow
    assert "a scientific runtime was embedded by mistake" in workflow
    assert "RuntimeFile" not in build
    assert "EmbedRuntime" not in build


def test_production_defaults_remain_public() -> None:
    build = read("build.ps1")
    setup = read("RankHunterSetup.ps1")
    user_bootstrap = read("bootstrap-user.sh")
    source = json.loads(read("build-source.json"))

    public_repo = "https://github.com/crhillresearch/rank-hunter.git"
    assert f'[string]$RepoUrl = "{public_repo}"' in build
    assert f'[string]$RepoUrl = "{public_repo}"' in setup
    assert f'REPO_URL="{public_repo}"' in user_bootstrap
    assert source["repo_url"] == public_repo
    assert source["repo_ref"] == "main"
    assert "rank-hunter-dev" not in build
    assert "oct-release" not in build


def test_setup_does_not_require_ubuntu_first_run() -> None:
    setup = read("RankHunterSetup.ps1")

    assert "Initialize-UbuntuUser" not in setup
    assert "Ubuntu needs its one-time Linux user setup" not in setup
    assert "RankHunter-Sage-10.10" in setup
    assert "Resolve-DistroUser" in setup




def test_broken_managed_sage_environment_is_repaired_without_touching_other_distros() -> None:
    setup = read("RankHunterSetup.ps1")
    package = read("RankHunter.iss")

    assert 'if (Test-Path $distroPath)' in setup
    assert 'Removing an incomplete Rank Hunter-owned WSL environment' in setup
    assert '& wsl.exe --unregister $ScienceDistroName' in setup
    assert "outside Rank Hunter's managed data directory" in setup
    assert "--unregister" not in package

def test_uninstaller_preserves_wsl_and_scientific_data() -> None:
    package = read("RankHunter.iss")

    assert "PrivilegesRequired=lowest" in package
    assert 'Name: "{localappdata}\\RankHunter"' in package
    assert "wsl --unregister" not in package
    assert "wsl.exe --unregister" not in package


def test_packaged_shell_scripts_are_forced_to_lf() -> None:
    attrs = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    build = read("build.ps1")

    assert "*.sh text eol=lf" in attrs
    assert "function Set-UnixLineEndings" in build
    assert '"bootstrap-prereqs.sh"' in build
    assert '"bootstrap-user.sh"' in build
    assert '"launch-rank-hunter.sh"' in build
    assert '"stop-rank-hunter.sh"' in build
    assert '.Replace("`r`n", "`n").Replace("`r", "`n")' in build


@pytest.mark.parametrize(
    "name",
    [
        "bootstrap-prereqs.sh",
        "bootstrap-user.sh",
        "launch-rank-hunter.sh",
        "stop-rank-hunter.sh",
    ],
)
def test_bash_helpers_parse(name: str) -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash unavailable")
    subprocess.run([bash, "-n", str(WIN / name)], check=True)

def test_public_release_tag_attaches_installer_to_github_release() -> None:
    workflow = (ROOT / ".github" / "workflows" / "build-windows-installer.yml").read_text(
        encoding="utf-8"
    )
    assert "Publish Windows installer to public GitHub Release" in workflow
    assert "github.repository == 'crhillresearch/rank-hunter'" in workflow
    assert "startsWith(github.ref, 'refs/tags/v')" in workflow
    assert "softprops/action-gh-release@v2" in workflow
    assert "files: installer/windows/dist/RankHunter-Setup-x64.exe" in workflow
    assert "contents: write" in workflow
