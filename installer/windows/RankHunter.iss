#ifndef RankHunterVersion
  #define RankHunterVersion "0.9.2-preview"
#endif

#define RankHunterName "Rank Hunter"
#define RankHunterPublisher "C. R. Hill"
#define RankHunterUrl "https://github.com/crhillresearch/rank-hunter"

[Setup]
AppId={{C9B145E7-19A2-4EF8-B88A-1D92BAE52B10}
AppName={#RankHunterName}
AppVersion={#RankHunterVersion}
AppPublisher={#RankHunterPublisher}
AppPublisherURL={#RankHunterUrl}
AppSupportURL={#RankHunterUrl}
AppUpdatesURL={#RankHunterUrl}
VersionInfoCompany={#RankHunterPublisher}
VersionInfoDescription=Rank Hunter Installer
VersionInfoProductName=Rank Hunter
SetupIconFile=RankHunter.ico
UninstallDisplayIcon={app}\RankHunter.ico
DefaultDirName={localappdata}\Programs\Rank Hunter
DefaultGroupName=Rank Hunter
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
MinVersion=10.0.19041
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=dist
OutputBaseFilename=RankHunter-Setup-x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=Rank Hunter
CreateAppDir=yes
CloseApplications=no

[InstallDelete]
Type: files; Name: "{autoprograms}\Rank Hunter.lnk"
Type: files; Name: "{autoprograms}\Rank Hunter Setup and Repair.lnk"
Type: files; Name: "{autoprograms}\Stop Rank Hunter.lnk"
Type: filesandordirs; Name: "{autoprograms}\Rank Hunter"

[Files]
Source: "RankHunterSetup.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "RankHunterSetupUi.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "RankHunterLauncher.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "RankHunterStop.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "RankHunter.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "RankHunterPreflight.ps1"; Flags: dontcopy
Source: "bootstrap-prereqs.sh"; DestDir: "{app}"; Flags: ignoreversion
Source: "bootstrap-user.sh"; DestDir: "{app}"; Flags: ignoreversion
Source: "launch-rank-hunter.sh"; DestDir: "{app}"; Flags: ignoreversion
Source: "stop-rank-hunter.sh"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "build-source.json"; DestDir: "{app}"; Flags: ignoreversion
#ifdef RankHunterSourceBundle
Source: "{#RankHunterSourceBundle}"; DestDir: "{app}\source"; DestName: "rank-hunter.bundle"; Flags: ignoreversion nocompression
#endif

[Run]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "{code:GetSetupUiParameters}"; Description: "Set up the selected Rank Hunter environments"; Flags: waituntilterminated
Filename: "{autoprograms}\Rank Hunter\Rank Hunter.lnk"; Description: "Run Rank Hunter on close"; Flags: postinstall nowait skipifsilent shellexec; Check: ShouldOfferRunRankHunter

[UninstallRun]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\RankHunterStop.ps1"""; Flags: runhidden; RunOnceId: "StopRankHunter"

[UninstallDelete]
Type: filesandordirs; Name: "{localappdata}\RankHunter"
Type: filesandordirs; Name: "{autoprograms}\Rank Hunter"

[Code]
var
  EnvironmentPage: TWizardPage;
  WslCheck: TNewCheckBox;
  ScienceCheck: TNewCheckBox;
  AppCheck: TNewCheckBox;
  WslStatus: TNewStaticText;
  ScienceStatus: TNewStaticText;
  AppStatus: TNewStaticText;
  EstimateText: TNewStaticText;
  PreflightDone: Boolean;
  WslReady: Boolean;
  ScienceReady: Boolean;
  RankHunterReady: Boolean;
  DetectedDistro: String;
  DetectedUser: String;
  DetectedSciencePython: String;

function Q(const S: String): String;
begin
  Result := S;
  StringChangeEx(Result, '"', '""', True);
  Result := '"' + Result + '"';
end;

procedure UpdateEstimate(Sender: TObject);
var
  MinMinutes, MaxMinutes: Integer;
begin
  MinMinutes := 0;
  MaxMinutes := 0;

  if WslCheck.Checked and WslCheck.Enabled then
  begin
    MinMinutes := MinMinutes + 2;
    MaxMinutes := MaxMinutes + 10;
  end;

  if ScienceCheck.Checked and ScienceCheck.Enabled then
  begin
    MinMinutes := MinMinutes + 5;
    MaxMinutes := MaxMinutes + 15;
  end;

  if AppCheck.Checked then
  begin
    MinMinutes := MinMinutes + 3;
    MaxMinutes := MaxMinutes + 10;
  end;

  if MaxMinutes = 0 then
    EstimateText.Caption := 'No installation work selected.'
  else
    EstimateText.Caption := Format('Estimated setup time: about %d-%d minutes (internet speed and PC performance vary).', [MinMinutes, MaxMinutes]);
end;

procedure ApplyPreflight;
var
  PreflightScript, PreflightIni, Params: String;
  ResultCode: Integer;
begin
  if PreflightDone then
    exit;

  PreflightDone := True;
  ExtractTemporaryFile('RankHunterPreflight.ps1');
  PreflightScript := ExpandConstant('{tmp}\RankHunterPreflight.ps1');
  PreflightIni := ExpandConstant('{tmp}\rankhunter-preflight.ini');

  Params := '-NoProfile -ExecutionPolicy Bypass -File ' + Q(PreflightScript) +
    ' -OutputPath ' + Q(PreflightIni);

  WizardForm.NextButton.Enabled := False;
  WslStatus.Caption := 'Detecting existing Windows/WSL environment...';
  ScienceStatus.Caption := 'Detecting SageMath...';
  AppStatus.Caption := 'Detecting Rank Hunter...';

  if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    Params, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    ResultCode := -1;
  end;

  if ResultCode = 0 then
  begin
    WslReady := GetIniString('preflight', 'wsl_ready', '0', PreflightIni) = '1';
    ScienceReady := GetIniString('preflight', 'science_ready', '0', PreflightIni) = '1';
    RankHunterReady := GetIniString('preflight', 'rank_hunter_ready', '0', PreflightIni) = '1';
    DetectedDistro := GetIniString('preflight', 'distro', '', PreflightIni);
    DetectedUser := GetIniString('preflight', 'distro_user', '', PreflightIni);
    DetectedSciencePython := GetIniString('preflight', 'science_python', '', PreflightIni);
  end
  else
  begin
    WslReady := False;
    ScienceReady := False;
    RankHunterReady := False;
  end;

  if WslReady then
  begin
    WslCheck.Checked := False;
    WslCheck.Enabled := False;
    if DetectedDistro <> '' then
      WslStatus.Caption := 'Detected: WSL 2 with ' + DetectedDistro + '. Existing Linux environment will be reused.'
    else
      WslStatus.Caption := 'Detected: WSL 2 is already enabled. No WSL installation needed.';
  end
  else
  begin
    WslCheck.Checked := True;
    WslCheck.Enabled := True;
    WslStatus.Caption := 'Not detected. Rank Hunter can enable WSL 2 automatically; Windows may require a restart.';
  end;

  if ScienceReady then
  begin
    ScienceCheck.Checked := False;
    ScienceCheck.Enabled := False;
    ScienceStatus.Caption := 'Detected: SageMath in ' + DetectedDistro + ' at ' + DetectedSciencePython + '. It will be reused.';
  end
  else
  begin
    ScienceCheck.Checked := True;
    ScienceCheck.Enabled := True;
    ScienceStatus.Caption := 'Not detected. Setup will download the official SageMath WSL image (~1.4 GB) itself and install it automatically.';
  end;

  AppCheck.Enabled := True;
  AppCheck.Checked := True;
  if RankHunterReady then
    AppStatus.Caption := 'Detected: Rank Hunter is already installed. Leave checked to update/repair it.'
  else
    AppStatus.Caption := 'Rank Hunter is not installed in the detected environment.';

  WizardForm.NextButton.Enabled := True;
  UpdateEstimate(nil);
end;

procedure InitializeWizard;
begin
  EnvironmentPage := CreateCustomPage(wpSelectDir,
    'Choose Rank Hunter environments',
    'Detected components are reused automatically. Uncheck anything you do not want installed.');

  WslCheck := TNewCheckBox.Create(EnvironmentPage);
  WslCheck.Parent := EnvironmentPage.Surface;
  WslCheck.Left := 0;
  WslCheck.Top := 8;
  WslCheck.Width := EnvironmentPage.SurfaceWidth;
  WslCheck.Caption := 'WSL 2 / Linux backend  (about 2-10 min if missing; restart may be required)';
  WslCheck.OnClick := @UpdateEstimate;

  WslStatus := TNewStaticText.Create(EnvironmentPage);
  WslStatus.Parent := EnvironmentPage.Surface;
  WslStatus.Left := 20;
  WslStatus.Top := WslCheck.Top + 25;
  WslStatus.Width := EnvironmentPage.SurfaceWidth - 20;
  WslStatus.Height := 34;
  WslStatus.WordWrap := True;
  WslStatus.Caption := 'Will detect before installation.';

  ScienceCheck := TNewCheckBox.Create(EnvironmentPage);
  ScienceCheck.Parent := EnvironmentPage.Surface;
  ScienceCheck.Left := 0;
  ScienceCheck.Top := 78;
  ScienceCheck.Width := EnvironmentPage.SurfaceWidth;
  ScienceCheck.Caption := 'SageMath scientific engine  (about 5-15 min; ~1.4 GB download if missing)';
  ScienceCheck.OnClick := @UpdateEstimate;

  ScienceStatus := TNewStaticText.Create(EnvironmentPage);
  ScienceStatus.Parent := EnvironmentPage.Surface;
  ScienceStatus.Left := 20;
  ScienceStatus.Top := ScienceCheck.Top + 25;
  ScienceStatus.Width := EnvironmentPage.SurfaceWidth - 20;
  ScienceStatus.Height := 34;
  ScienceStatus.WordWrap := True;
  ScienceStatus.Caption := 'Will detect before installation.';

  AppCheck := TNewCheckBox.Create(EnvironmentPage);
  AppCheck.Parent := EnvironmentPage.Surface;
  AppCheck.Left := 0;
  AppCheck.Top := 148;
  AppCheck.Width := EnvironmentPage.SurfaceWidth;
  AppCheck.Caption := 'Rank Hunter application  (about 3-10 min)';
  AppCheck.OnClick := @UpdateEstimate;

  AppStatus := TNewStaticText.Create(EnvironmentPage);
  AppStatus.Parent := EnvironmentPage.Surface;
  AppStatus.Left := 20;
  AppStatus.Top := AppCheck.Top + 25;
  AppStatus.Width := EnvironmentPage.SurfaceWidth - 20;
  AppStatus.Height := 34;
  AppStatus.WordWrap := True;
  AppStatus.Caption := 'Will detect before installation.';

  EstimateText := TNewStaticText.Create(EnvironmentPage);
  EstimateText.Parent := EnvironmentPage.Surface;
  EstimateText.Left := 0;
  EstimateText.Top := 220;
  EstimateText.Width := EnvironmentPage.SurfaceWidth;
  EstimateText.Height := 40;
  EstimateText.WordWrap := True;
  EstimateText.Font.Style := [fsBold];
  EstimateText.Caption := 'Detecting existing environments...';

  PreflightDone := False;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = EnvironmentPage.ID then
    ApplyPreflight;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID <> EnvironmentPage.ID then
    exit;

  if (not WslReady) and (not WslCheck.Checked) and (ScienceCheck.Checked or AppCheck.Checked) then
  begin
    MsgBox('WSL 2 is required for SageMath and Rank Hunter. Either check the WSL 2 option or uncheck the dependent components.',
      mbError, MB_OK);
    Result := False;
    exit;
  end;

  if (not ScienceReady) and (not ScienceCheck.Checked) and AppCheck.Checked then
  begin
    MsgBox('Rank Hunter requires a SageMath scientific environment. Either check SageMath or uncheck Rank Hunter.',
      mbError, MB_OK);
    Result := False;
    exit;
  end;

  if (not WslCheck.Checked) and (not ScienceCheck.Checked) and (not AppCheck.Checked) then
  begin
    MsgBox('Nothing is selected for installation.', mbInformation, MB_OK);
    Result := False;
  end;
end;

function ShouldOfferRunRankHunter: Boolean;
begin
  Result := AppCheck.Checked;
end;

function GetSetupUiParameters(Param: String): String;
var
  SetupUi: String;
begin
  SetupUi := ExpandConstant('{app}\RankHunterSetupUi.ps1');
  Result := '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File ' + Q(SetupUi);

  Result := Result + ' -CustomSelection';
  if WslCheck.Checked and WslCheck.Enabled then
    Result := Result + ' -InstallWsl';
  if ScienceCheck.Checked and ScienceCheck.Enabled then
    Result := Result + ' -InstallScience';
  if AppCheck.Checked then
    Result := Result + ' -InstallRankHunter';
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    Log('Rank Hunter setup shell installed. Start Menu shortcuts are created only after environment and application verification succeed.');
  end;
end;
