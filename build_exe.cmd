@echo off
rem exe 版を dist\ShellMenuKeeper.exe に作る。PyInstaller が必要: python -m pip install pyinstaller
cd /d "%~dp0"
python -m PyInstaller --noconfirm --clean --onefile --windowed --name ShellMenuKeeper --distpath dist --workpath build --specpath build shellmenu_app.py
if errorlevel 1 exit /b 1
echo.
echo dist\ShellMenuKeeper.exe
