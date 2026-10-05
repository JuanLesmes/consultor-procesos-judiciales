@echo off
setlocal
cd /d "%~dp0"
echo === Consultor de Procesos: instalacion en este equipo ===
echo.

where py >nul 2>nul
if %errorlevel%==0 (
  set "PY=py -3"
) else (
  where python >nul 2>nul
  if %errorlevel%==0 (
    set "PY=python"
  ) else (
    echo No se encontro Python. Instale Python 3.11 o superior desde https://www.python.org/downloads/
    echo y marque la casilla "Add python.exe to PATH" durante la instalacion. Luego vuelva a ejecutar este archivo.
    pause
    exit /b 1
  )
)

%PY% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if not %errorlevel%==0 (
  echo La version de Python instalada es anterior a 3.11. Instale una mas reciente desde https://www.python.org/downloads/
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creando el entorno virtual .venv ...
  %PY% -m venv .venv
  if not %errorlevel%==0 (
    echo No se pudo crear el entorno virtual.
    pause
    exit /b 1
  )
)

if exist "wheels\" (
  echo Instalando sin internet desde la carpeta wheels ...
  ".venv\Scripts\python.exe" -m pip install --no-index --find-links wheels consultor_procesos -q
) else (
  echo Instalando desde internet ...
  ".venv\Scripts\python.exe" -m pip install -e . -q
)
if not %errorlevel%==0 (
  echo La instalacion fallo. Si no hay internet, asegurese de copiar tambien la carpeta wheels.
  pause
  exit /b 1
)

if not exist "consultor_procesos.toml" (
  ".venv\Scripts\consultor-procesos.exe" iniciar-config
  echo Se creo consultor_procesos.toml. Edite la linea agente_usuario con su correo de contacto.
)

echo.
echo Instalacion terminada. Para abrir la interfaz ejecute iniciar_interfaz.bat
pause
