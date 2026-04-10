@echo off
REM Get directory of this script and move to parent (same as SCRIPT_DIR/..)
cd /d %~dp0\..

REM Activate environment
call venv\Scripts\activate

REM Run application
python sd_viewer\main_gui.py

echo.
pause