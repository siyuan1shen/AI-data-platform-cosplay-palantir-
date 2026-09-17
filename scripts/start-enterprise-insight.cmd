@echo off
setlocal

rem Desktop-friendly launcher: bypasses the user's PowerShell execution policy
rem for this local project only, then keeps the window open when startup fails.
cd /d "%~dp0.."
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0desktop-launch.ps1"
if errorlevel 1 (
  echo.
  echo Enterprise Insight failed to start.
  echo See %%LOCALAPPDATA%%\EnterpriseInsight\desktop-launch-error.log for details.
  pause
)

endlocal
