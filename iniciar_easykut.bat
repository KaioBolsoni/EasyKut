@echo off
title EasyKut Studio
cd /d "%~dp0"
set "PATH=%USERPROFILE%\.deno\bin;%PATH%"

echo [1/4] Liberando porta 8502...
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8502 -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.OwningProcess -gt 0 } | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"

echo [2/4] Verificando ambiente virtual...
if not exist "venv\Scripts\python.exe" goto sem_venv

echo [3/4] Atualizando yt-dlp para a versao mais recente...
"venv\Scripts\python.exe" -m pip install -U yt-dlp --quiet --disable-pip-version-check || echo Aviso: nao foi possivel atualizar o yt-dlp agora, continuando...

echo [4/4] Iniciando EasyKut Studio e abrindo no navegador...
start "" /b cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:8502"
"venv\Scripts\python.exe" -m uvicorn server:app --host 127.0.0.1 --port 8502
pause
exit /b 0

:sem_venv
echo ERRO: Ambiente virtual nao encontrado em venv\Scripts\python.exe
pause
exit /b 1
