@echo off
rem Starts the reconciliation app on this laptop: database and Redis (Docker),
rem the website (Django, port 8000) and the background worker (Celery), each
rem in its own window, then opens the browser. Close with stop_app.bat.
rem Development / UAT use only; production runs these as Windows services.

setlocal
cd /d "%~dp0"
title SUCO Reconciliation - starting

if not exist ".venv\Scripts\python.exe" (
    echo The Python environment .venv was not found in %CD%.
    echo Ask IT or the developer to set it up.
    pause
    exit /b 1
)

rem --- Docker (database and Redis) -------------------------------------------
docker info >nul 2>&1
if not errorlevel 1 goto docker_ready
echo Docker is not running. Starting Docker Desktop...
if not exist "%ProgramFiles%\Docker\Docker\Docker Desktop.exe" (
    echo Docker Desktop was not found. Start it from the Start menu, then run this file again.
    pause
    exit /b 1
)
start "" "%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
echo Waiting for Docker to be ready, this can take a minute...
set /a tries=0
:wait_docker
timeout /t 5 /nobreak >nul
docker info >nul 2>&1
if not errorlevel 1 goto docker_ready
set /a tries+=1
if %tries% lss 36 goto wait_docker
echo Docker did not start within 3 minutes. Start Docker Desktop by hand, then run this file again.
pause
exit /b 1

:docker_ready
echo Starting the database and Redis...
docker compose up -d
if errorlevel 1 (
    echo The database could not be started. See the messages above.
    pause
    exit /b 1
)

rem --- Already running? -------------------------------------------------------
netstat -ano | findstr /r /c:":8000 .*LISTENING" >nul
if not errorlevel 1 (
    echo Something is already running on port 8000 - the app may already be open.
    echo Opening the browser. Run stop_app.bat first if you want a clean start.
    start "" http://localhost:8000/
    pause
    exit /b 0
)

rem --- Pending database changes (warn only, never applied automatically) -------
".venv\Scripts\python.exe" manage.py migrate --check >nul 2>&1
if errorlevel 1 (
    echo.
    echo WARNING: the code has database changes ^(migrations^) not applied yet.
    echo Ask the developer before running "python manage.py migrate".
    echo The app will start anyway.
    echo.
)

rem --- Website and worker, each in its own window -----------------------------
start "SUCO Reconciliation - Website" cmd /k ".venv\Scripts\python.exe manage.py runserver"
start "SUCO Reconciliation - Worker" cmd /k ".venv\Scripts\celery.exe -A config worker --pool=solo -l info"

echo Waiting for the website to answer...
set /a tries=0
:wait_web
timeout /t 2 /nobreak >nul
powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing http://localhost:8000/ -TimeoutSec 3 | Out-Null; exit 0 } catch { exit 1 }"
if not errorlevel 1 goto web_ready
set /a tries+=1
if %tries% lss 30 goto wait_web
echo The website did not answer within a minute. Look at the "Website" window for errors.
pause
exit /b 1

:web_ready
start "" http://localhost:8000/
echo.
echo The app is running at http://localhost:8000/
echo Keep the "Website" and "Worker" windows open. To stop, run stop_app.bat.
timeout /t 10
exit /b 0
