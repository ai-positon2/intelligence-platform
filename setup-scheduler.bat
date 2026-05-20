@echo off
:: ─────────────────────────────────────────────────────────────────────────────
::  Signal Tracker — Windows Task Scheduler setup
::  Registers a task that runs main.py every Monday at 08:00 AM
::  Run this file ONCE as Administrator to register the task.
:: ─────────────────────────────────────────────────────────────────────────────

SET TASK_NAME=SignalTrackerWeeklyRun
SET PROJECT_DIR=C:\Users\krishna.l\company-signal-tracker
SET PYTHON=python
SET SCRIPT=%PROJECT_DIR%\main.py

echo.
echo Setting up Windows Task Scheduler for Signal Tracker...
echo Task:    %TASK_NAME%
echo Runs:    Every Monday at 05:00 PM
echo Script:  %SCRIPT%
echo.

:: Delete existing task if it exists (clean re-register)
schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1

:: Create the scheduled task
schtasks /create ^
  /tn "%TASK_NAME%" ^
  /tr "\"%PYTHON%\" \"%SCRIPT%\" --sheets-only" ^
  /sc WEEKLY ^
  /d MON ^
  /st 17:00 ^
  /rl HIGHEST ^
  /f

IF %ERRORLEVEL% EQU 0 (
    echo.
    echo ✅ Task registered successfully!
    echo.
    echo The tracker will run every Monday at 05:00 PM automatically.
    echo To run it manually right now:
    echo   schtasks /run /tn "%TASK_NAME%"
    echo.
    echo To check task status:
    echo   schtasks /query /tn "%TASK_NAME%"
    echo.
    echo To remove the task:
    echo   schtasks /delete /tn "%TASK_NAME%" /f
) ELSE (
    echo.
    echo ❌ Failed to register task. Try running this file as Administrator.
    echo Right-click setup-scheduler.bat ^> Run as administrator
)

pause
