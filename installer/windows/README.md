# Rank Hunter Windows installer

This directory builds the Windows front end for Rank Hunter while keeping the
scientific runtime in **WSL2 + Ubuntu**.

The Windows installer is intentionally not a native port of SageMath, PARI,
eclib, ratpoints, or Rank Hunter's scientific subprocesses. It provides the
Windows-native installation and launch experience around the Linux environment
Rank Hunter already targets.

## User experience

A release build produces a small normal Windows installer:

```text
RankHunter-Setup-x64.exe
```

Before anything is installed, setup shows three explicit environment choices:

- **WSL 2 / Linux backend** — about 2-10 minutes if missing; Windows may require a restart.
- **SageMath scientific engine** — about 5-15 minutes if missing; approximately 1.4 GB is downloaded by setup.
- **Rank Hunter application** — about 3-10 minutes.

Existing compatible components are detected first. Detected WSL/SageMath installs
are reused rather than replaced. The checkboxes control installation: if a
missing component is unchecked, Rank Hunter does not install it. Setup blocks
invalid combinations, such as requesting Rank Hunter while declining a missing
SageMath environment.

The SageMath path follows the same Windows/WSL pattern used by SageMath's own
`sage-binder-env` Windows installer: the small installer downloads a pinned
ready-made SageMath WSL image, verifies its SHA256, extracts it, and imports it
with `wsl --import`. Rank Hunter does **not** solve a 389-package conda
environment on the user's machine.

The current pinned scientific image is SageMath 10.10:

```text
https://github.com/sagemath/sage-binder-env/releases/download/v10.10/sagemath-10.10-wsl.zip
SHA256 87d2f2542a48ab0bee07defcad196335576b3c1b621233f5461a9aedd44edff6
```

The setup window shows real download percentage plus an elapsed-time heartbeat.
Detailed Linux output is written to
`%LOCALAPPDATA%\RankHunter\setup.log` and is available through **Show details**.

Inno Setup itself creates no Start-menu application shortcuts. After WSL,
SageMath, Rank Hunter, and `scripts/run-ui.sh` have all passed verification,
the setup worker creates exactly one visible Start-menu shortcut:

```text
Rank Hunter
```

There is no premature Setup/Repair or Stop shortcut. If installation fails,
there is no dead Rank Hunter menu entry.

## First install on a machine without WSL

If WSL 2 is missing and the user leaves that option checked, Rank Hunter runs
Microsoft's `wsl --install --no-distribution`. Windows may require a restart.
The installer records resumable state under:

```text
%LOCALAPPDATA%\RankHunter\setup-state.json
```

If a restart is required, no Rank Hunter shortcuts are created. Rerun the same
installer after Windows restarts.

Rank Hunter does not require a throwaway Ubuntu first-run account just to obtain
SageMath. When no compatible existing Sage environment is found, setup imports
the pinned SageMath WSL image as Rank Hunter's own WSL distribution.

## Existing WSL / SageMath machine

Preflight searches existing WSL distributions for a usable non-root user and a
Python that can import `sage.all`. Compatible existing SageMath installations
are reused rather than replaced. The detected distribution, user, and scientific
Python path are persisted so launch/stop operations use the same environment
later.

## Data / uninstall boundary

The Windows uninstaller removes the Windows setup shell, Start Menu shortcuts,
and installer state. It does **not** unregister WSL distributions and does not
delete the Rank Hunter checkout or `rank42.db`.

Rank Hunter-owned WSL scientific data lives under:

```text
%LOCALAPPDATA%\RankHunter\wsl
```

The Windows uninstaller does not unregister the WSL distribution, so removing the
Windows shell cannot silently destroy the scientific environment or Rank Hunter data.

## Build locally

Install Inno Setup 6, then from PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File installer\windows\validate.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File installer\windows\build.ps1
```

Or let the build helper install Inno Setup through winget:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File installer\windows\build.ps1 -InstallInno
```

For release-candidate validation, CI bundles the exact checked-out source commit
into the installer while keeping the installed checkout's origin pointed at the
public Rank Hunter repository:

```text
https://github.com/crhillresearch/rank-hunter.git
```

This lets the curated release tree be tested before publication without making a
private development repository part of the installed product. The final
production installer must use the immutable public release tag (for example
`v0.9.2`) as its repository ref.

## Scientific runtime source

The normal Windows installer currently pins the official public SageMath WSL
image from `sagemath/sage-binder-env`:

```text
SageMath version: 10.10
WSL distro name:  RankHunter-Sage-10.10
Download size:    about 1.4 GB
```

The ZIP URL and SHA256 are stored in `build-source.json` and baked into the
installer manifest. The download happens from inside the setup flow only when
preflight cannot find a compatible SageMath environment. The package is
checksum-verified before import and removed from the download cache after a
successful import.

## Windows support boundary

Target: Windows 10 version 2004 / build 19041 or newer, or Windows 11, on x64.

GPU ratpoints remains optional. The normal Rank Hunter installer builds CPU
ratpoints. GPU support is attempted only when the WSL environment already has
a usable NVIDIA/CUDA toolchain, matching the existing Linux installer policy.

## Files

- `RankHunter.iss` — Inno Setup package definition.
- `RankHunterSetup.ps1` — selectable WSL/Sage/Rank Hunter setup worker.
- `RankHunterSetupUi.ps1` — Windows progress/status UI and log viewer.
- `RankHunterPreflight.ps1` — detects existing WSL, SageMath, and Rank Hunter environments.
- `RankHunterLauncher.ps1` — Start Menu launcher and browser handoff.
- `RankHunterStop.ps1` — clean UI stop command.
- `bootstrap-prereqs.sh` — installs only the Linux packages Rank Hunter needs.
- `bootstrap-user.sh` — Rank Hunter checkout/update + existing installer.
- `launch-rank-hunter.sh` / `stop-rank-hunter.sh` — WSL process helpers.
- `build.ps1` — local Inno Setup compiler wrapper and package-manifest writer.
- `validate.ps1` — static package validation.
