@echo off
rem ---------------------------------------------------------------------------
rem Para o assistente que esteja a correr sem janela (pythonw.exe).
rem ---------------------------------------------------------------------------

echo A procurar o assistente...

tasklist /fi "imagename eq pythonw.exe" | find /i "pythonw.exe" >nul
if errorlevel 1 (
    echo Nao esta a correr nenhum processo pythonw.exe.
    pause
    exit /b 0
)

taskkill /f /im pythonw.exe
echo.
echo Assistente parado.
pause
