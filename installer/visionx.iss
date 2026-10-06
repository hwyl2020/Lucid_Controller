; Inno Setup script: VisionX Setup.exe (wraps the PyInstaller folder dist\VisionX).
; Built by:  .venv\Scripts\python -m installer.build   (passes AppVersion, AppPublisher)
;
; - Installs for all users (Program Files, needs admin) or just the current user (no admin);
;   Setup asks. App data then lives in Documents\VisionX (Program Files) or next to the .exe.
; - Start menu shortcut, optional desktop shortcut, uninstaller (user data is kept).
; - Warns when the LUCID Arena SDK is missing (the app needs it to find cameras).
; - Optional Windows Firewall rule for VisionX.exe (all-users install only): GigE image
;   data arrives over the network, so a blocked app may not stream.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef AppPublisher
  #define AppPublisher "HWYL"
#endif
#define AppName "VisionX"
#define AppExe "VisionX.exe"
#define FirewallRule "VisionX (GigE camera streams)"

[Setup]
; AppId identifies the product for upgrades/uninstall: never change it.
AppId={{710631DE-C648-4B44-999C-F02D81917F6C}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppCopyright=Copyright (C) {#AppPublisher}
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
SetupIconFile=..\app\resources\app.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
CloseApplications=yes
OutputDir=..\dist
OutputBaseFilename={#AppName}_Setup_{#AppVersion}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "firewall"; Description: "Allow VisionX through Windows Firewall (recommended for GigE cameras)"; \
  GroupDescription: "Network:"; Check: IsAdminInstallMode

[Files]
Source: "..\dist\{#AppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autoprograms}\{#AppName} - Read me first"; Filename: "{app}\README_FIRST.txt"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{sys}\netsh.exe"; \
  Parameters: "advfirewall firewall add rule name=""{#FirewallRule}"" dir=in action=allow program=""{app}\{#AppExe}"" enable=yes profile=private,domain"; \
  Flags: runhidden; Tasks: firewall; StatusMsg: "Adding a Windows Firewall rule..."
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""{#FirewallRule}"""; \
  Flags: runhidden; RunOnceId: "RemoveFirewallRule"; Check: IsAdminInstallMode

[Code]
const
  ArenaKey = 'SOFTWARE\Lucid Vision Labs\Arena SDK';

function ArenaSdkInstalled: Boolean;
var
  Folder: String;
begin
  Result := RegQueryStringValue(HKLM64, ArenaKey, 'InstallFolder', Folder) and (Folder <> '');
end;

procedure InitializeWizard;
begin
  if not ArenaSdkInstalled then
    CreateOutputMsgPage(wpWelcome,
      'LUCID Arena SDK not found',
      'VisionX needs the LUCID Arena SDK to find and control cameras.',
      'The Arena SDK (64-bit) does not seem to be installed on this PC.' + #13#10#13#10 +
      'You can continue installing VisionX now, then install the Arena SDK from LUCID Vision Labs ' +
      '(thinklucid.com > Downloads Hub > Arena SDK for Windows) with its default options, ' +
      'including the LUCID Lightweight Filter Driver for GigE cameras.' + #13#10#13#10 +
      'Without the SDK, VisionX opens but cannot find any cameras.');
end;
