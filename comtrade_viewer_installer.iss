; Inno Setup script for COMTRADE Viewer
; Build source: dist\comtrade_viewer
; Optional: place vc_redist.x64.exe рядом с этим .iss, если хотите включить VC++ Redistributable в установщик.

#define MyAppName "COMTRADE Viewer"
#define MyAppVersion "6.1"
#define MyAppPublisher "Local Build"
#define MyAppExeName "comtrade_viewer.exe"
#define MyAppSourceDir "dist\\comtrade_viewer"

[Setup]
AppId={{5E5D77D1-5C89-4F5D-9EE7-4D9F12A4C1B2}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
DisableProgramGroupPage=yes
LicenseFile=
InfoBeforeFile=
InfoAfterFile=
OutputDir=installer_output
OutputBaseFilename=comtrade_viewer_setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupIconFile=

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Дополнительные значки:"; Flags: unchecked

[Files]
Source: "{#MyAppSourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Необязательное включение VC++ Redistributable, если файл лежит рядом со скриптом.
Source: "vc_redist.x64.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: FileExists(ExpandConstant('{src}\\vc_redist.x64.exe')); ExternalSize: 0

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Если redistributable добавлен рядом со скриптом, установим его тихо перед первым запуском приложения.
Filename: "{tmp}\vc_redist.x64.exe"; Parameters: "/install /quiet /norestart"; StatusMsg: "Установка Microsoft Visual C++ Redistributable..."; Flags: waituntilterminated; Check: FileExists(ExpandConstant('{tmp}\\vc_redist.x64.exe'))
Filename: "{app}\{#MyAppExeName}"; Description: "Запустить {#MyAppName}"; Flags: nowait postinstall skipifsilent
