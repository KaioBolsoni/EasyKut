@echo off
chcp 65001 > nul
cd /d "%~dp0"

echo ========================================================
echo        EASYKUT - INICIALIZANDO APLICACAO STREAMLIT
echo ========================================================
echo.

:: 1. Verificar se a porta 8502 ja esta em uso e encerrar processo antigo de forma silenciosa
echo [1/3] Verificando e liberando porta 8502...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$conn = Get-NetTCPConnection -LocalPort 8502 -ErrorAction SilentlyContinue; if ($conn) { $pids = $conn | Select-Object -ExpandProperty OwningProcess -Unique; foreach ($p in $pids) { if ($p -gt 0) { Stop-Process -Id $p -Force -ErrorAction SilentlyContinue } } }" > nul 2>&1

:: 2. Verificar se o ambiente virtual existe
echo [2/3] Verificando ambiente virtual (venv)...
if not exist "venv\Scripts\python.exe" (
    echo.
    echo ========================================================
    echo [ERRO] O ambiente virtual nao foi encontrado!
    echo Nao foi possivel localizar: venv\Scripts\python.exe
    echo.
    echo Certifique-se de que o ambiente virtual (venv) foi criado
    echo antes de iniciar a aplicacao.
    echo ========================================================
    echo.
    pause
    exit /b 1
)

:: 3. Executar o Streamlit usando diretamente o Python do ambiente virtual
echo [3/3] Iniciando Streamlit na porta 8502...
echo Abrindo EasyKut no navegador...
echo.

".\venv\Scripts\python.exe" -m streamlit run app.py --server.port 8502

if %errorlevel% neq 0 (
    echo.
    echo ========================================================
    echo [AVISO] A aplicacao foi encerrada com erro (codigo %errorlevel%).
    echo ========================================================
    echo.
    pause
)
