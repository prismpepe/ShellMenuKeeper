@echo off
cd /d "%~dp0"
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw -m shellmenu
) else (
    py -3 -m shellmenu
)
