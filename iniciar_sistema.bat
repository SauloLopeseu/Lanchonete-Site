@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
title Sistema Lanchonete - API e Impressora
color 0A

cd /d "%~dp0"

echo ======================================================
echo   INICIANDO SISTEMA DA LANCHONETE (API + IMPRESSORA)
echo ======================================================
echo.

REM Verifica arquivos principais antes de iniciar
if not exist "main.py" (
    echo [ERRO] Arquivo main.py nao encontrado em "%~dp0".
    pause
    exit /b 1
)

if not exist "printer_service.py" (
    echo [AVISO] Arquivo printer_service.py nao encontrado. Pulando servico de impressora.
    set "RUN_PRINTER=0"
) else (
    set "RUN_PRINTER=1"
)

REM Carrega variaveis do arquivo .env se existir
if exist ".env" (
    echo [0/4] Carregando variaveis de ambiente do .env...
    for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
        set "LINE=%%A"
        if not "!LINE!"=="" if not "!LINE:~0,1!"=="#" (
            set "%%A=%%B"
        )
    )
)

REM Detecta ambiente virtual (.venv ou venv)
set "VENV_ACTIVATE="
if exist ".venv\Scripts\activate.bat" set "VENV_ACTIVATE=%~dp0.venv\Scripts\activate.bat"
if not defined VENV_ACTIVATE if exist "venv\Scripts\activate.bat" set "VENV_ACTIVATE=%~dp0venv\Scripts\activate.bat"

if defined VENV_ACTIVATE (
    echo [1/4] Ativando ambiente virtual...
    call "%VENV_ACTIVATE%"
) else (
    echo [1/4] Usando Python global do Windows...
)

REM Verifica se o Python esta acessivel
where python >nul 2>&1
if errorlevel 1 (
    echo [ERRO] Python nao encontrado no PATH do Windows.
    pause
    exit /b 1
)

echo [2/4] Verificando e instalando dependencias...
python -m pip install --upgrade --quiet pip
python -m pip install --quiet fastapi uvicorn sqlalchemy pydantic httpx python-escpos pyusb
if errorlevel 1 (
    echo [ERRO] Falha ao instalar dependencias Python. Verifique sua conexao ou ambiente.
    pause
    exit /b 1
)

REM Inicia servico de impressora termica em janela separada
if "%RUN_PRINTER%"=="1" (
    echo [3/4] Iniciando Servico de Impressao Termica...
    if defined VENV_ACTIVATE (
        start "Impressora Termica - Lanchonete" cmd /k "cd /d "%~dp0" && call "%VENV_ACTIVATE%" && python printer_service.py"
    ) else (
        start "Impressora Termica - Lanchonete" cmd /k "cd /d "%~dp0" && python printer_service.py"
    )
) else (
    echo [3/4] Servico de Impressao Termica ignorado.
)

echo [4/4] Iniciando Servidor FastAPI (Cardapio e Painel Admin)...
echo.
echo  - Cardapio Digital:   http://127.0.0.1:8000
echo  - Painel de Pedidos:  http://127.0.0.1:8000/admin
echo.

REM Abre o Painel de Pedidos apos 3 segundos
start "" cmd /c "timeout /t 3 /nobreak >nul && start http://127.0.0.1:8000/admin"

python main.py

echo.
echo Servidor encerrado.
pause