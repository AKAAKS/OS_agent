@echo off
:: OS Agent — Remove auto-start task
:: Run this script as Administrator

echo ============================================
echo   OS Agent — Remove Auto-Start
echo ============================================
echo.

:: Stop the task if running
schtasks /end /tn "OS_Agent" >nul 2>&1

:: Delete the task
schtasks /delete /tn "OS_Agent" /f

if %errorlevel%==0 (
    echo.
    echo SUCCESS: OS Agent auto-start removed.
) else (
    echo.
    echo Task "OS_Agent" not found or already removed.
)

echo.
pause
