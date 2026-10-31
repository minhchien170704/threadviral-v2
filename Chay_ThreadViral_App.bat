@echo off
title GHIEN DOC COMMENT - AI VIDEO MAKER v3.0
echo ======================================================
echo    DANG KHOI DONG THREADVIRAL STUDIO v3.0...
echo ======================================================
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" app_launcher.py
) else (
    cd /d "%~dp0dist\ThreadViral"
    start "" "ThreadViral.exe"
)
pause
