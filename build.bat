@echo off
rem Gera a pasta portatil em dist\Cortai (precisa de Python e de ffmpeg no PATH).
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe python -m venv .venv || goto fail
.venv\Scripts\python -m pip install -q -r requirements-build.txt || goto fail
rem O PyInstaller recria dist\Cortai; preserva a escolha da pasta de dados.
if exist dist\Cortai\dados.txt copy /y dist\Cortai\dados.txt .dados.txt.bak >nul
.venv\Scripts\python -m PyInstaller --noconfirm cortai.spec || goto fail
if exist .dados.txt.bak move /y .dados.txt.bak dist\Cortai\dados.txt >nul
echo.
echo Pronto: dist\Cortai\Cortai.exe
pause
exit /b 0
:fail
echo.
echo O build falhou.
pause
exit /b 1
