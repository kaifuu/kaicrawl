; Inno Setup 安装包脚本 —— 把 dist\KaiCrawl 绿色目录打成单文件安装程序。
;
; 用法：
;   1) 先 python build_exe.py 产出 dist\KaiCrawl\（见 build\发布说明.md）
;   2) 安装 Inno Setup 6（https://jrsoftware.org/isdl.php，Win7 可装可编译）
;   3) 双击本文件 → Compile（或命令行 ISCC.exe 本文件），得 dist\KaiCrawl_Setup.exe
;
; 关键点：
;   PrivilegesRequired=lowest + 装到 {localappdata}：不需要管理员权限，
;   避免 Program Files 下 data/ 目录无写权限导致 SQLite/文档输出失败。

#define MyAppName "KaiCrawl"
#define MyAppVersion "1.0.0"
#define MyAppExeName "KaiCrawl.exe"

[Setup]
AppId={{52A7C9E3-8B41-4F6D-9A2C-3D1E5B7F0C4A}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=KaiCrawl
DefaultDirName={localappdata}\KaiCrawl
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; 普通用户权限即可安装（Win7 家用机常常没有管理员账号密码）
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=KaiCrawl_Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Win7 x86/x64 均可安装
ArchitecturesAllowed=x86 x64
; 数据（data\crawler.db、data\output\）存 EXE 旁，卸载时默认保留，删数据请手动

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式(&D)"; GroupDescription: "附加任务:"

[Files]
Source: "..\dist\KaiCrawl\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即运行 {#MyAppName}"; Flags: nowait postinstall skipifsilent
