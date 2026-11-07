@echo off
rem Stops the website and the background worker started by start_app.bat.
rem The database and Redis stay up in Docker (no data is lost either way).

setlocal
cd /d "%~dp0"

echo Stopping the website and the worker...
taskkill /FI "WINDOWTITLE eq SUCO Reconciliation - Website*" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq SUCO Reconciliation - Worker*" /T /F >nul 2>&1

netstat -ano | findstr /r /c:":8000 .*LISTENING" >nul
if not errorlevel 1 (
    echo Something is still running on port 8000 - it was not started by start_app.bat.
    echo Close that window by hand.
) else (
    echo Stopped.
)
timeout /t 5
exit /b 0
