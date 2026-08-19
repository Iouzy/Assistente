; Instalador do Assistente (Inno Setup).
;
; Compila-se com:  iscc /DMyAppVersion=1.0.0 instalador.iss
; (é o que o .github/workflows/compilar.yml faz, depois do PyInstaller.)
;
; Porquê um instalador e não o `.exe` solto: um `.exe` largado no Ambiente de
; Trabalho não sabe criar atalhos, não aparece em «Aplicações Instaladas», não
; se desinstala e não sabe fechar a versão antiga antes de se substituir. O
; instalador faz as quatro coisas — e é a última delas que torna a
; actualização automática possível.

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

#define MyAppName "Assistente"
#define MyAppExeName "Assistente.exe"

[Setup]
AppId={{8E3B6A54-5E6C-4B2F-9E3D-2C7A1F4B9D10}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppName}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=dist
OutputBaseFilename=Assistente-instalador-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

; `lowest` e não `admin`: instala em %LOCALAPPDATA%\Programs para o
; utilizador actual, sem pedir permissões de administrador. É um assistente
; pessoal, num computador pessoal — e sem o pedido de elevação a
; actualização automática podia correr sem ninguém ter de carregar em «Sim».
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; Fecha o painel e o bot antes de substituir os ficheiros, e volta a abri-los
; no fim. É isto que faz `/CLOSEAPPLICATIONS /RESTARTAPPLICATIONS` funcionar
; quando é o próprio painel a lançar o instalador (ver actualizacao.py).
CloseApplications=yes
RestartApplications=yes
CloseApplicationsFilter=*.exe

[Languages]
Name: "portugues"; MessagesFile: "compiler:Languages\Portuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar um atalho no Ambiente de Trabalho"; \
    GroupDescription: "Atalhos:"
Name: "arranque"; Description: "Abrir o painel quando iniciar sessão no Windows"; \
    GroupDescription: "Arranque:"; Flags: unchecked

[Files]
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Desinstalar o {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: arranque

[Run]
Filename: "{app}\{#MyAppExeName}"; \
    Description: "Abrir o painel de controlo"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; A pasta de dados (%LOCALAPPDATA%\Assistente) **não** é apagada de
; propósito: lá dentro estão a base de dados com a agenda e as notas, e o
; `.env` com as credenciais. Desinstalar o programa não é o mesmo que querer
; perder os dados, e quem os quiser mesmo fora apaga a pasta à mão — o painel
; tem um botão «Pasta» que a abre.
Type: dirifempty; Name: "{app}"
