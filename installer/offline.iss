[Setup]
#ifdef IntegrationTest
AppId={{4CB2FA1B-D59C-4E31-8BCC-2387D79B919C}
AppName=RVC Studio Offline Installer Test
OutputDir=..\build\installer-offline-test
OutputBaseFilename=Installer-Offline-Test-Only
#else
AppId={{0F5D528C-CE48-4D6D-8672-9EB1946738E0}
AppName=RVC Studio
OutputDir=..\dist
OutputBaseFilename=RVCStudio-Setup-0.3.0-offline-inner
#endif
AppVersion=0.3.0
AppVerName=RVC Studio 0.3.0 离线全量版
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
InfoBeforeFile=INSTALL-NOTICE-OFFLINE.txt
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
; 注意：约 4.93 GB 的引擎分片不内嵌进本 Setup.exe（Inno 单文件上限约 4.2 GB）。
; 它们与本 Setup.exe 同处一个离线文件夹（{src}，由离线压缩包完整解压得到），安装时从该目录本地合并。
Source: "..\build\app\RVCStudio.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\build\app\RVCSetupHelper.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\studio\USER_GUIDE.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\studio\licenses\*"; DestDir: "{app}\licenses"; Flags: ignoreversion recursesubdirs createallsubdirs

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
  ResultPath, CancelPath, HelperPath, DataPath, TmpPath, ChunkPath: String;

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
  if Stage = 'MERGE' then MessageText := '正在合并内置引擎分片（本地操作，不联网）'
  else if Stage = 'DOWNLOAD' then MessageText := '正在下载内置变声引擎（约 4.93 GB，可断点续传）'
  else if Stage = 'VERIFY' then MessageText := '正在校验引擎完整性（SHA-256）'
  else if Stage = 'EXTRACT' then MessageText := '正在解压并配置内置引擎'
  else if Stage = 'DEPENDENCIES' then MessageText := '正在检测引擎依赖，首次加载可能需要几分钟'
  else if Stage = 'READY' then MessageText := '变声引擎已准备完成';
  ProgressPage.SetText(MessageText, '全程离线，无需另外安装或下载 Applio。');
  ProgressPage.SetProgress(Value, 100);
end;

procedure CancelPreparation(Sender: TObject);
begin
  SaveStringToFile(CancelPath, 'cancel', False);
  AbortButton.Enabled := False;
  ProgressPage.SetText('正在取消，已解压内容将保留', '请等待当前步骤返回。');
end;

procedure InitializeWizard;
begin
  ProgressPage := CreateOutputProgressPage('正在部署离线变声组件', '引擎、女声模型与虚拟麦克风均已内置。');
  AbortButton := TNewButton.Create(ProgressPage);
  AbortButton.Parent := ProgressPage.Surface;
  AbortButton.Caption := '取消准备';
  AbortButton.SetBounds(0, ScaleY(150), ScaleX(120), ScaleY(30));
  AbortButton.OnClick := @CancelPreparation;
  DataPath := ExpandConstant('{localappdata}\RVCStudio');
#ifdef IntegrationTest
  DataPath := ExpandConstant('{param:TESTDATA|{tmp}\RVCStudio-Test}');
#endif
  TmpPath := ExpandConstant('{tmp}');
  ChunkPath := ExpandConstant('{src}');
#ifdef IntegrationTest
  ChunkPath := ExpandConstant('{param:CHUNKDIR|{src}}');
#endif
  ResultPath := ExpandConstant('{tmp}\rvc-setup-result.ini');
  CancelPath := ExpandConstant('{tmp}\rvc-setup-cancel');
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var Code: Integer; Params, ErrorText: String; Started: Boolean;
begin
  Result := '';
  if EngineReady and DriverReady then Exit;
  ExtractTemporaryFile('RVCSetupHelper.exe');
  HelperPath := ExpandConstant('{tmp}\RVCSetupHelper.exe');
  DeleteFile(CancelPath);
  Provisioning := True;
  AbortButton.Enabled := True;
  AbortButton.Visible := True;
  ProgressPage.SetText('正在本地部署内置变声引擎（约 4.93 GB 分片）', '全程离线，建议预留 25 GB 磁盘空间。');
  ProgressPage.SetProgress(0, 100);
  ProgressPage.Show;
  try
    if not EngineReady then begin
      DeleteFile(ResultPath);
      if not FileExists(AddBackslash(ChunkPath) + 'engine.bin.001') then begin
        Result := '未在安装程序同目录找到内置引擎分片（engine.bin.001）。' + #13#10 + '请把离线压缩包完整解压到同一个文件夹，使本 Setup.exe 与 engine.bin.001～004 在一起后再运行，不要把 Setup.exe 单独复制出来。';
        Exit;
      end;
      Params := 'engine --data-root "' + DataPath + '" --result "' + ResultPath + '" --cancel-file "' + CancelPath + '" --engine-chunk-dir "' + ChunkPath + '"';
      Started := ExecAndLogOutput(HelperPath, Params, '', SW_HIDE, ewWaitUntilTerminated, Code, @HelperOutput);
      if not Started then begin Result := '无法启动引擎安装组件：' + SysErrorMessage(Code); Exit; end;
      if (Code <> 0) or (GetIniString('Result', 'status', '', ResultPath) <> 'ok') then begin
        ErrorText := GetIniString('Result', 'error', '引擎安装未成功，退出码：' + IntToStr(Code), ResultPath);
        Result := ErrorText + #13#10 + '安装尚未完成，可以重新运行本安装包重试。';
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
    '离线全量安装，将在本地完成（无需联网）：' + NewLine +
    '  1. 合并、校验并部署内置变声引擎（约 4.93 GB 分片，展开约 7.5 GB）。' + NewLine +
    '  2. 预置两把自然普通话女声模型（主：标准 v2，备：HQ/Ov2）。' + NewLine +
    '  3. 安装或复用 VB-CABLE 虚拟音频驱动。' + NewLine +
    '  4. 安装中文变声界面。' + NewLine + NewLine +
    '无需单独安装 Applio 或 VB-CABLE，也无需下载模型。建议预留 25 GB 空间。' + NewLine +
    '驱动首次安装后可能需要重启。' + NewLine + NewLine +
    'VB-CABLE 来自 VB-Audio，是 donationware，欢迎捐赠支持。' + NewLine +
    'https://vb-audio.com/Cable/';
end;
