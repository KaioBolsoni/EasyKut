@echo off
title EasyKut Studio
cd /d "%~dp0"
set "PATH=%USERPROFILE%\.deno\bin;%PATH%"

echo [1/3] Liberando porta 8502...
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8502 -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.OwningProcess -gt 0 } | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"

echo [2/3] Verificando ambiente virtual...
if not exist "venv\Scripts\python.exe" goto sem_venv

if /I "%~1"=="--update-ytdlp" (
    echo [Opcional] Atualizando yt-dlp conforme solicitado via --update-ytdlp...
    "venv\Scripts\python.exe" -m pip install -U yt-dlp --quiet --disable-pip-version-check || echo Aviso: falha ao atualizar yt-dlp, continuando...
)

echo [3/3] Iniciando EasyKut Studio e abrindo no navegador...
start "" /b cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:8502"
"venv\Scripts\python.exe" -m uvicorn server:app --host 127.0.0.1 --port 8502
pause
exit /b 0

:sem_venv
echo ERRO: Ambiente virtual nao encontrado em venv\Scripts\python.exe
pause
exit /b 1
