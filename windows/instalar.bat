@echo off
rem ---------------------------------------------------------------------------
rem Prepara o assistente para correr em Windows: cria o ambiente virtual,
rem instala as dependencias (bot + painel) e um atalho no Ambiente de
rem Trabalho para abrir o painel com um duplo clique, sem mais nada depois
rem disto.
rem
rem Corre-se uma vez, com duplo clique neste ficheiro (ou windows\instalar.bat
rem a partir de uma consola).
rem ---------------------------------------------------------------------------

cd /d "%~dp0.."

where python >nul 2>nul
if errorlevel 1 (
    echo.
    echo ERRO: nao encontrei o Python. Instale-o de https://python.org
    echo ^(marque "Add python.exe to PATH" no instalador^)
    echo.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo A criar o ambiente virtual em .venv...
    python -m venv .venv
)

echo A instalar as dependencias ^(bot + painel^)...
".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt -r requirements-painel.txt

rem Atalho no Ambiente de Trabalho, com o caminho absoluto deste repositorio
rem ja preenchido - so faz sentido gerado aqui, cada pessoa clona para uma
rem pasta diferente. Feito por PowerShell porque o .bat nao sabe criar .lnk.
powershell -NoProfile -Command ^
    "$c = New-Object -ComObject WScript.Shell;" ^
    "$s = $c.CreateShortcut(\"$env:USERPROFILE\Desktop\Assistente - Painel de Controlo.lnk\");" ^
    "$s.TargetPath = '%~dp0painel.vbs';" ^
    "$s.WorkingDirectory = '%~dp0..';" ^
    "$s.Description = 'Ligar, desligar e configurar o assistente pessoal';" ^
    "$s.Save()"

echo.
echo Pronto. Ja tens um atalho no Ambiente de Trabalho:
echo   Assistente - Painel de Controlo
echo.
echo As credenciais ^(token do Telegram, chave da DeepSeek^) preenchem-se na
echo aba «Credenciais» do painel - nao e preciso editar nenhum ficheiro.
echo.
pause
