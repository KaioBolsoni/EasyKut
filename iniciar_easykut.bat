@echo off
title EasyKut Studio
cd /d "%~dp0"

echo [1/3] Liberando porta 8502...
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8502 -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.OwningProcess -gt 0 } | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"

echo [2/3] Verificando ambiente virtual...
if not exist "venv\Scripts\python.exe" goto sem_venv

echo [3/3] Abrindo EasyKut Studio no navegador...
start http://127.0.0.1:8502
"venv\Scripts\python.exe" -m uvicorn server:app --host 127.0.0.1 --port 8502
pause
exit /b 0

:sem_venv
echo ERRO: Ambiente virtual nao encontrado em venv\Scripts\python.exe
pause
exit /b 1
