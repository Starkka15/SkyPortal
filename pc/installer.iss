; Setup for SkyPortal, the Portal of Power for the Skylanders PC ports.
;
; Installs PyInstaller's folder build (pc\dist\SkyPortal\) per user, without
; administrator rights. Settings (registry), placed figures (AppData) and the
; figure library (Documents\Skylanders) are the user's and stay on uninstall.
;
; Built by pc\build.py, which passes the version:
;   ISCC /DAppVersion=0.2.0 pc\installer.iss      -> pc\release\

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{BCDE96AB-6BF9-4F8C-9407-902B60A47C4C}
AppName=SkyPortal
AppVersion={#AppVersion}
AppVerName=SkyPortal {#AppVersion}
AppPublisherURL=https://github.com/Starkka15/SkyPortal
AppSupportURL=https://github.com/Starkka15/SkyPortal/issues
DefaultDirName={autopf}\SkyPortal
DefaultGroupName=SkyPortal
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
LicenseFile=..\LICENSE
OutputDir=release
OutputBaseFilename=SkyPortal-{#AppVersion}-Setup
SetupIconFile=skyportal.ico
UninstallDisplayIcon={app}\SkyPortal.exe
UninstallDisplayName=SkyPortal
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Close a running SkyPortal before replacing or removing it.
CloseApplications=yes

[Tasks]
Name: desktopicon; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "dist\SkyPortal\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; The previous version's bundled libraries, so no stale ones are left beside new ones.
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{group}\SkyPortal"; Filename: "{app}\SkyPortal.exe"
Name: "{autodesktop}\SkyPortal"; Filename: "{app}\SkyPortal.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\SkyPortal.exe"; Description: "{cm:LaunchProgram,SkyPortal}"; Flags: nowait postinstall skipifsilent
