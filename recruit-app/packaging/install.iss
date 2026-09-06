; RecruitApp 单机版安装脚本（spec §4.1/§4.6/§4.8）。开发机 Windows 侧构建（Inno Setup 6）。
; 构建前先照 BUILD.md（Task 9 Step 4）组好 stage\app：
;   python\（便携 3.12 + venv site-packages）+ backend\ + web\ + browsers\ + run.bat + 本 vbs
#define MyAppName "招聘系统"
#define MyAppVersion "0.9.0"
#define MyAppPublisher "华联招聘团队"
#define StageDir "stage"

[Setup]
AppId={{0F7E5C2A-8D9B-4C21-A6F3-9B1D2E4F5A60}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\RecruitApp
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=output
OutputBaseFilename=recruit-app-setup-{#MyAppVersion}
Compression=lzma2/ultra
SolidCompression=yes
UninstallDisplayName={#MyAppName}
WizardStyle=modern
; 卸载保留 data\（含 Fernet 密钥/导出/上传）；[Code] 卸载末尾提示用户手动删除
CloseApplications=no

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加图标:"

[Files]
Source: "{#StageDir}\app\*"; DestDir: "{app}\app"; Flags: recursesubdirs createallsubdirs ignoreversion; Excludes: "__pycache__,*.pyc,.venv*"
Source: "{#StageDir}\app\data\*"; DestDir: "{app}\data"; Flags: recursesubdirs createallsubdirs skipifsourcedoesntexist; Excludes: "__pycache__,*.pyc"

[Icons]
Name: "{autodesktop}\招聘系统"; Filename: "wscript.exe"; Parameters: """{app}\app\RecruitApp.launch.vbs"""; WorkingDir: "{app}\app"; Tasks: desktopicon; Comment: "启动招聘系统（本机单机版）"

[Run]
Filename: "schtasks.exe"; Parameters: "/Create /F /TN ""RecruitApp"" /TR """"{app}\app\run.bat"""" /SC ONLOGON /RL LIMITED"; Flags: runhidden; StatusMsg: "注册开机自启（当前用户登录时）…"
Filename: "wscript.exe"; Parameters: """{app}\app\RecruitApp.launch.vbs"""; Flags: nowait skipifsilent; StatusMsg: "启动招聘系统…"; Description: "立即启动招聘系统"

[UninstallDelete]
; 升级改名残留的 app.old 一并清掉（data\ 永不在此列）
Type: filesandordirs; Name: "{app}\app.old"

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Result := '';
  // 只回收本程序自己的后端进程：pythonw.exe 且命令行含 backend.main:app → 精确 PID kill。
  // 绝不 taskkill /IM 整类、绝不碰用户其它进程（spec §4.4 + 全局约束）。
  // 注意：整段必须包在 try/catch 里——裸 for/if/exit 写法经 Inno Exec 传给 powershell -Command
  // 会被解析失败（实测 Setup 上下文 exit code=1，本机 Task 9 Step 5 复验捕获）。try/catch 结构实测稳定。
  Exec('powershell.exe',
    '-NoProfile -ExecutionPolicy Bypass -Command "try { ' +
    'for ($i = 0; $i -lt 3; $i++) { ' +
    '  $p = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq ''pythonw.exe'' -and $_.CommandLine -match ''backend.main:app'' }); ' +
    '  if ($p.Count -eq 0) { break }; ' +
    '  $p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; ' +
    '  Start-Sleep -Milliseconds 400 ' +
    '}; ' +
    'if ($p.Count -gt 0) { throw ''still running'' } ' +
    '} catch { exit 1 }"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  if ResultCode <> 0 then
  begin
    Result := '未能完全停止旧版本的后端进程（进程可能被占用或权限不足）。请手动关闭正在运行的招聘系统后，重新运行安装程序。';
    Exit;
  end;
  // 上一次升级遗留的 app.old 备份：直接删除（数据在 {app}\data，不在备份内）。
  // 否则第二次跨版本升级时 RenameFile 会撞已存在的 app.old 而失败。
  if DirExists(ExpandConstant('{app}\app.old')) then
    DelTree(ExpandConstant('{app}\app.old'), True, True, True);
  // 升级：旧 app\ 改名 app.old（Inno 随后全新覆盖写 app\）；data\ 不在其中，天然保留。
  // 首装时无 app\，判存在再改名。
  if DirExists(ExpandConstant('{app}\app')) then
    if not RenameFile(ExpandConstant('{app}\app'), ExpandConstant('{app}\app.old')) then
      Result := '无法备份旧版本目录（app.old）。' + #13#10 +
                '请关闭正在运行的招聘系统后重试；如仍失败请手动把 ' +
                ExpandConstant('{app}\app') + ' 改名后重装。';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    MsgBox('卸载完成。你的数据（含加密密钥、导出文件、上传简历）仍在 ' + #13#10 +
           ExpandConstant('{app}\data') + #13#10 + #13#10 +
           '如需彻底删除请手动删除整个 RecruitApp 文件夹。', mbInformation,
           MB_OK);
end;
