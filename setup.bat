@echo off
cd /d %~dp0

echo Setting up environment...

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo Python is not installed. Please install Python 3 and try again.
    pause
    exit /b
)

REM Create virtual environment
python -m venv venv

REM Activate environment
call venv\Scripts\activate

REM Install dependencies
python -m pip install --upgrade pip
pip install -r requirements.txt

echo.
echo Setup complete.
echo Double-click run_viewer.bat to start the application.
pause