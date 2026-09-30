[Setup]
AppId={{48563E1B-5D88-4072-953D-8B61A79A6C12}
AppName=RVC Studio Preview
AppVersion=0.1.0
AppVerName=RVC Studio 0.1.0 Preview
DefaultDirName={localappdata}\Programs\RVCStudio
DefaultGroupName=RVC Studio
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist
OutputBaseFilename=RVCStudio-Setup-0.1.0-preview
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\RVCStudio.exe
InfoBeforeFile=..\studio\USER_GUIDE.txt
CloseApplications=yes

[Languages]
Name: "chinesesimp"; MessagesFile: "ChineseSimplified.isl"

[Files]
Source: "..\build\app\RVCStudio.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\studio\USER_GUIDE.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\studio\licenses\*"; DestDir: "{app}\licenses"; Flags: ignoreversion recursesubdirs

[Icons]
Name: "{group}\RVC Studio"; Filename: "{app}\RVCStudio.exe"
Name: "{group}\Uninstall RVC Studio"; Filename: "{uninstallexe}"
Name: "{autodesktop}\RVC Studio"; Filename: "{app}\RVCStudio.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; Flags: unchecked

[Run]
Filename: "{app}\RVCStudio.exe"; Description: "启动 RVC Studio"; Flags: nowait postinstall skipifsilent runasoriginaluser
