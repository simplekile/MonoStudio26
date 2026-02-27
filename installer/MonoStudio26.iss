; Inno Setup 6 — MonoStudio 26 installer
; Run after: pyinstaller monostudio26.spec
; Compile: iscc installer\MonoStudio26.iss   (or open in Inno Setup Compiler)

#define MyAppName "MonoStudio 26"
#define MyAppExe "MonoStudio26.exe"
; Source = PyInstaller onedir output (relative to this script's parent = repo root)
#define SourceDir "..\dist\MonoStudio26"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion=26.0.0
DefaultDirName={autopf}\MonoStudio26
DefaultGroupName={#MyAppName}
OutputDir=..\dist
OutputBaseFilename=MonoStudio26_Setup
SetupIconFile=..\monostudio_data\icons\app.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=currentUser
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
