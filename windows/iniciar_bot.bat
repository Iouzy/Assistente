@echo off
rem ---------------------------------------------------------------------------
rem Arranca o assistente com a janela visivel (util para testar).
rem Funciona a partir de qualquer pasta: %~dp0 e a pasta deste ficheiro.
rem ---------------------------------------------------------------------------

cd /d "%~dp0.."

rem Sem janela nao ha onde ver os registos, por isso garantimos um ficheiro.
if "%LOG_FILE%"=="" set LOG_FILE=%~dp0..\assistente.log

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo ERRO: nao encontrei o ambiente virtual em .venv
    echo Crie-o com:  python -m venv .venv
    echo E instale as dependencias com:  .venv\Scripts\pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

if not exist ".env" (
    echo.
    echo ERRO: falta o ficheiro .env com as credenciais.
    echo Copie o exemplo com:  copy .env.example .env
    echo.
    pause
    exit /b 1
)

echo A iniciar o assistente... (feche esta janela ou prima Ctrl+C para parar)
echo.
".venv\Scripts\python.exe" main.py

rem Se o bot terminar por erro, a janela fica aberta para se poder ler a causa.
if errorlevel 1 (
    echo.
    echo O assistente terminou com erro. Veja as mensagens acima.
    pause
)
