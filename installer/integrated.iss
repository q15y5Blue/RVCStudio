[Setup]
#ifdef IntegrationTest
AppId={{44A47C15-D730-4354-B016-35BF524D0E13}
AppName=RVC Studio Installer Test
OutputDir=..\build\installer-test
OutputBaseFilename=Installer-Test-Only
#else
AppId={{48563E1B-5D88-4072-953D-8B61A79A6C12}
AppName=RVC Studio
OutputDir=..\dist
OutputBaseFilename=RVCStudio-Setup-0.2.0-online
#endif
AppVersion=0.2.0
AppVerName=RVC Studio 0.2.0 Beta
DefaultDirName={localappdata}\Programs\RVCStudio
DefaultGroupName=RVC Studio
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.16299
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\RVCStudio.exe
InfoBeforeFile=INSTALL-NOTICE.txt
LicenseFile=..\studio\licenses\VB-CABLE-EULA.txt
CloseApplications=yes
RestartIfNeededByRun=no
SetupLogging=yes
SetupMutex=RVCStudioIntegratedSetup

[Languages]
Name: "chinesesimp"; MessagesFile: "ChineseSimplified.isl"

[Files]
#ifdef IntegrationTest
Source: "..\build\test-helper\RVCSetupHelper.exe"; Flags: dontcopy
#else
Source: "..\build\app\RVCSetupHelper.exe"; Flags: dontcopy
#endif
Source: "..\build\app\RVCStudio.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\build\app\RVCSetupHelper.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\studio\USER_GUIDE.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\studio\licenses\*"; DestDir: "{app}\licenses"; Flags: ignoreversion recursesubdirs

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; Flags: unchecked

[Icons]
Name: "{group}\RVC Studio"; Filename: "{app}\RVCStudio.exe"
Name: "{group}\卸载 RVC Studio"; Filename: "{uninstallexe}"
Name: "{autodesktop}\RVC Studio"; Filename: "{app}\RVCStudio.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\RVCStudio.exe"; Description: "启动 RVC Studio"; Flags: nowait postinstall skipifsilent runasoriginaluser; Check: CanLaunch

[Code]
var
  ProgressPage: TOutputProgressWizardPage;
  AbortButton: TNewButton;
  Provisioning, EngineReady, DriverReady, DriverReboot: Boolean;
  ResultPath, CancelPath, HelperPath, DataPath: String;

function TakeField(var S: String): String;
var P: Integer;
begin
  P := Pos('|', S);
  if P = 0 then begin Result := S; S := ''; end
  else begin Result := Copy(S, 1, P-1); Delete(S, 1, P); end;
end;

procedure HelperOutput(const S: String; const Error, FirstLine: Boolean);
var Rest, Stage, MessageText: String; Value: Integer;
begin
  Log(S);
  if Pos('RVC|', S) <> 1 then Exit;
  Rest := Copy(S, 5, Length(S));
  Stage := TakeField(Rest);
  Value := StrToIntDef(TakeField(Rest), 0);
  MessageText := '正在准备内置变声引擎';
  if Stage = 'DOWNLOAD' then MessageText := '正在下载内置变声引擎（约 4.93 GB，可断点续传）';
  if Stage = 'VERIFY' then MessageText := '正在校验引擎完整性';
  if Stage = 'EXTRACT' then MessageText := '正在解压并配置内置引擎';
  if Stage = 'DEPENDENCIES' then MessageText := '正在检测引擎依赖，首次加载可能需要几分钟';
  if Stage = 'READY' then MessageText := '变声引擎已准备完成';
  ProgressPage.SetText(MessageText, '无需另外安装或启动 Applio。');
  ProgressPage.SetProgress(Value, 100);
end;

procedure CancelPreparation(Sender: TObject);
begin
  SaveStringToFile(CancelPath, 'cancel', False);
  AbortButton.Enabled := False;
  ProgressPage.SetText('正在取消，已下载内容将保留', '请等待当前网络请求返回。');
end;

procedure InitializeWizard;
begin
  ProgressPage := CreateOutputProgressPage('自动配置变声组件', '所有组件由此安装程序统一配置。');
  AbortButton := TNewButton.Create(ProgressPage);
  AbortButton.Parent := ProgressPage.Surface;
  AbortButton.Caption := '取消准备';
  AbortButton.SetBounds(0, ScaleY(150), ScaleX(120), ScaleY(30));
  AbortButton.OnClick := @CancelPreparation;
  DataPath := ExpandConstant('{localappdata}\RVCStudio');
#ifdef IntegrationTest
  DataPath := ExpandConstant('{param:TESTDATA|{tmp}\RVCStudio-Test}');
#endif
  ResultPath := ExpandConstant('{tmp}\rvc-setup-result.ini');
  CancelPath := ExpandConstant('{tmp}\rvc-setup-cancel');
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var Code: Integer; Params, ErrorText, ArchivePath: String; Started: Boolean;
begin
  Result := '';
  if EngineReady and DriverReady then Exit;
  ExtractTemporaryFile('RVCSetupHelper.exe');
  HelperPath := ExpandConstant('{tmp}\RVCSetupHelper.exe');
  DeleteFile(CancelPath);
  Provisioning := True;
  AbortButton.Enabled := True;
  AbortButton.Visible := True;
  ProgressPage.SetText('准备下载、校验并安装内置变声引擎', '需要网络连接，建议预留 20 GB 空间。');
  ProgressPage.SetProgress(0, 100);
  ProgressPage.Show;
  try
    if not EngineReady then begin
      DeleteFile(ResultPath);
      Params := 'engine --data-root "' + DataPath + '" --result "' + ResultPath + '" --cancel-file "' + CancelPath + '"';
      ArchivePath := ExpandConstant('{param:ENGINEARCHIVE|}');
      if ArchivePath <> '' then Params := Params + ' --archive "' + ArchivePath + '"';
      Started := ExecAndLogOutput(HelperPath, Params, '', SW_HIDE, ewWaitUntilTerminated, Code, @HelperOutput);
      if not Started then begin Result := '无法启动引擎安装组件：' + SysErrorMessage(Code); Exit; end;
      if (Code <> 0) or (GetIniString('Result', 'status', '', ResultPath) <> 'ok') then begin
        ErrorText := GetIniString('Result', 'error', '引擎安装未成功，退出码：' + IntToStr(Code), ResultPath);
        Result := ErrorText + #13#10 + '安装尚未完成。可以重试，已经下载的内容将保留。';
        Exit;
      end;
      EngineReady := True;
    end;
    if FileExists(CancelPath) then begin Result := '安装准备已取消。'; Exit; end;
    if not DriverReady then begin
      AbortButton.Visible := False;
      ProgressPage.SetText('正在安装 VB-CABLE 虚拟麦克风', '如出现 Windows 管理员授权窗口，请选择“是”。此步骤可能需要几分钟。');
      ProgressPage.SetProgress(0, 0);
      DeleteFile(ResultPath);
      Params := 'driver --result "' + ResultPath + '"';
      Started := ShellExec('runas', HelperPath, Params, '', SW_HIDE, ewWaitUntilTerminated, Code);
      if not Started then begin Result := '虚拟麦克风安装未完成：' + SysErrorMessage(Code) + #13#10 + '请重试并允许管理员授权。'; Exit; end;
      if GetIniString('Result', 'status', '', ResultPath) <> 'ok' then begin
        Result := GetIniString('Result', 'error', '驱动组件没有报告成功；请重试。', ResultPath);
        Exit;
      end;
      DriverReboot := GetIniString('Result', 'reboot', '0', ResultPath) = '1';
      DriverReady := True;
    end;
    if FileExists(CancelPath) then begin Result := '安装准备已取消。'; Exit; end;
  finally
    Provisioning := False;
    ProgressPage.Hide;
  end;
end;

procedure CancelButtonClick(CurPageID: Integer; var Cancel, Confirm: Boolean);
begin
  if Provisioning then begin
    Cancel := False;
    Confirm := False;
    CancelPreparation(nil);
  end;
end;

function NeedRestart: Boolean;
begin
  Result := DriverReboot;
end;

function CanLaunch: Boolean;
begin
  Result := not DriverReboot;
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo, MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
begin
  Result := MemoDirInfo + NewLine + NewLine +
    '将自动完成：' + NewLine +
    '  1. 下载并配置内置变声引擎（约 4.93 GB）。' + NewLine +
    '  2. 安装或复用 VB-CABLE 虚拟音频驱动。' + NewLine +
    '  3. 安装中文变声界面。' + NewLine + NewLine +
    '无需单独安装 Applio 或 VB-CABLE。驱动首次安装后可能需要重启。' + NewLine +
    '女声模型由你在软件中导入。' + NewLine + NewLine +
    'VB-CABLE 来自 VB-Audio，是 donationware，欢迎捐赠支持。' + NewLine +
    'https://vb-audio.com/Cable/';
end;
