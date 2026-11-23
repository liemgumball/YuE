@echo off
rem Start YuE2 Studio and open it in the browser. If it is already running, just open the page.
cd /d "%~dp0.."
title YuE2 Studio
netstat -ano | findstr /r /c:":7860 .*LISTENING" >nul
if %errorlevel%==0 (
  start "" http://127.0.0.1:7860
  exit /b
)
start "" /b cmd /c "timeout /t 2 /nobreak >nul & start "" http://127.0.0.1:7860"
echo YuE2 Studio dang chay tai http://127.0.0.1:7860  (dong cua so nay de tat)
".venv\Scripts\python.exe" tools\webui.py
pause
