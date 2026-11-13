@echo off
setlocal
cd /d "%~dp0"
"%~dp0whisper\.venv\Scripts\pythonw.exe" "%~dp0whisper\tekan_untuk_bicara.py" --browser
