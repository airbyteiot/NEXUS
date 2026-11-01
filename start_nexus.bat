@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo NEXUS environment missing. Run install_windows.ps1 first.
  pause
  exit /b 1
)
curl -s http://localhost:11434/api/tags >nul 2>&1
if errorlevel 1 (
  start "Ollama" "" "%LOCALAPPDATA%\Programs\Ollama\ollama app.exe"
  timeout /t 5 /nobreak >nul
)
".venv\Scripts\python.exe" -m streamlit run app.py
pause
