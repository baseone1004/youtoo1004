; Inno Setup 스크립트 — 배포판/유튜브영상자동제작/ 폴더를 설치 프로그램(exe)으로 묶는다.
; 사용법: Inno Setup 6 설치 (https://jrsoftware.org/isinfo.php) → 이 파일을 열고 Compile.
; 먼저 python 배포/빌드.py --no-zip 으로 배포 폴더를 만들어 두어야 한다.
#define AppName "유튜브 영상 자동 제작"
#define AppVersion GetDateTimeString('yyyy.mm.dd', '', '')
#define SrcDir "..\배포판\유튜브영상자동제작"

[Setup]
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={autopf}\유튜브영상자동제작
DefaultGroupName={#AppName}
OutputDir=..\배포판
OutputBaseFilename=유튜브영상자동제작_설치
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
WizardStyle=modern

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Files]
Source: "{#SrcDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\시작.bat"; WorkingDir: "{app}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\시작.bat"; WorkingDir: "{app}"

[Run]
Filename: "{app}\시작.bat"; Description: "지금 실행"; Flags: postinstall nowait skipifsilent shellexec
