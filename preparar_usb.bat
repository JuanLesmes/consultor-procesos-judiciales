@echo off
setlocal
cd /d "%~dp0"
echo === Consultor de Procesos: copiar a una USB u otra carpeta ===
echo.
echo Se copiara todo lo necesario para instalarlo en otro equipo (sin el entorno .venv,
echo que no es portable). Incluye la base de datos con sus procesos y la configuracion.
echo Si la interfaz esta abierta, cierrela antes de continuar.
echo.
set /p DESTINO=Carpeta de destino (por ejemplo E:\ConsultorDeProcesos):
if "%DESTINO%"=="" (
  echo No indico ninguna carpeta.
  pause
  exit /b 1
)

rem Consolida en el archivo principal lo que la base de datos tenga pendiente en su diario (WAL).
if exist ".venv\Scripts\python.exe" if exist "consultor_procesos.sqlite" (
  ".venv\Scripts\python.exe" -c "import sqlite3; c = sqlite3.connect('consultor_procesos.sqlite'); c.execute('PRAGMA wal_checkpoint(TRUNCATE)'); c.close()" >nul 2>nul
)

robocopy "%~dp0." "%DESTINO%" /E /XD .venv __pycache__ .pytest_cache .git build /XF *.pyc /NFL /NDL /NJH /NP
if %errorlevel% geq 8 (
  echo La copia fallo (codigo %errorlevel%).
  pause
  exit /b 1
)
echo.
echo Copia terminada en "%DESTINO%".
echo En el otro equipo: instale Python 3.11 o superior si no lo tiene, copie la carpeta al disco
echo local, ejecute instalar.bat y luego iniciar_interfaz.bat.
pause
