@echo off
:: OS Agent — Install as auto-start task via Windows Task Scheduler
:: Run this script as Administrator

echo ============================================
echo   OS Agent — Auto-Start Setup
echo ============================================
echo.

:: Get the Python path
for /f "delims=" %%i in ('where pythonw 2^>nul') do set PYTHON=%%i
if "%PYTHON%"=="" (
    echo ERROR: pythonw not found in PATH.
    echo Make sure Python is installed and added to PATH.
    pause
    exit /b 1
)

:: Set paths (remove trailing backslash for XML)
set AGENT_DIR=%~dp0
set AGENT_DIR=%AGENT_DIR:~0,-1%
set SCRIPT=%AGENT_DIR%\main.py

echo Python:     %PYTHON%
echo Agent dir:  %AGENT_DIR%
echo Script:     %SCRIPT%
echo.

:: Remove existing task if present
schtasks /delete /tn "OS_Agent" /f >nul 2>&1

:: Create XML task definition (supports working directory)
set XML_FILE=%TEMP%\os_agent_task.xml

(
echo ^<?xml version="1.0" encoding="UTF-16"?^>
echo ^<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task"^>
echo   ^<Triggers^>
echo     ^<LogonTrigger^>
echo       ^<Enabled^>true^</Enabled^>
echo     ^</LogonTrigger^>
echo   ^</Triggers^>
echo   ^<Principals^>
echo     ^<Principal^>
echo       ^<RunLevel^>HighestAvailable^</RunLevel^>
echo     ^</Principal^>
echo   ^</Principals^>
echo   ^<Settings^>
echo     ^<MultipleInstancesPolicy^>IgnoreNew^</MultipleInstancesPolicy^>
echo     ^<DisallowStartIfOnBatteries^>false^</DisallowStartIfOnBatteries^>
echo     ^<StopIfGoingOnBatteries^>false^</StopIfGoingOnBatteries^>
echo     ^<ExecutionTimeLimit^>PT0S^</ExecutionTimeLimit^>
echo     ^<RestartOnFailure^>
echo       ^<Interval^>PT1M^</Interval^>
echo       ^<Count^>3^</Count^>
echo     ^</RestartOnFailure^>
echo   ^</Settings^>
echo   ^<Actions^>
echo     ^<Exec^>
echo       ^<Command^>%PYTHON%^</Command^>
echo       ^<Arguments^>"%SCRIPT%"^</Arguments^>
echo       ^<WorkingDirectory^>%AGENT_DIR%^</WorkingDirectory^>
echo     ^</Exec^>
echo   ^</Actions^>
echo ^</Task^>
) > "%XML_FILE%"

:: Register the task from XML
schtasks /create /tn "OS_Agent" /xml "%XML_FILE%" /f
set TASK_RESULT=%errorlevel%

:: Clean up
del "%XML_FILE%" >nul 2>&1

if %TASK_RESULT%==0 (
    echo.
    echo SUCCESS: OS Agent will start automatically on login.
    echo.
    echo Key settings:
    echo   - Working directory: %AGENT_DIR%
    echo   - Runs on battery: YES (won't stop)
    echo   - Auto-restart on crash: 3 retries, 1 min apart
    echo   - No time limit
    echo.
    echo To manage:
    echo   Start now:    schtasks /run /tn "OS_Agent"
    echo   Stop:         schtasks /end /tn "OS_Agent"
    echo   Remove:       schtasks /delete /tn "OS_Agent" /f
    echo   View status:  schtasks /query /tn "OS_Agent" /v
    echo.
) else (
    echo.
    echo FAILED: Could not create scheduled task.
    echo Try running this script as Administrator.
    echo.
)

pause
