@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\consultor-procesos.exe" (
  echo No se encontro el entorno virtual. Instale primero con:
  echo    python -m venv .venv
  echo    .venv\Scripts\pip install -e .
  pause
  exit /b 1
)
if not exist "consultor_procesos.toml" (
  ".venv\Scripts\consultor-procesos.exe" iniciar-config
  echo Se creo consultor_procesos.toml. Edite la linea agente_usuario con su correo de contacto.
)
echo Abriendo la interfaz en el navegador. Deje esta ventana abierta; cierrela o pulse Ctrl+C para detener.
".venv\Scripts\consultor-procesos.exe" web %*
pause
