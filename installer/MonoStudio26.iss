; Inno Setup 6 — MonoStudio 26 installer
; Run after: pyinstaller monostudio26.spec
; Compile: iscc /DMyAppVersion=26.0.21 installer\MonoStudio26.iss   (build_installer.ps1 passes version from git)

#ifndef MyAppVersion
#define MyAppVersion "26.0.0"
#endif

#ifndef MyAppName
#define MyAppName "MonoStudio 26"
#endif

#ifndef MyOutputBaseFilename
#define MyOutputBaseFilename "MonoStudio26_Setup"
#endif

#define MyAppExe "MonoStudio26.exe"
; Source = PyInstaller onedir output (relative to this script's parent = repo root)
#define SourceDir "..\dist\MonoStudio26"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={autopf}\MonoStudio26
DefaultGroupName={#MyAppName}
OutputDir=..\dist
OutputBaseFilename={#MyOutputBaseFilename}
SetupIconFile=..\monostudio_data\icons\app.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
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
