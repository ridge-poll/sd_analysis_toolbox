@echo off
cd /d %~dp0

REM Activate environment
call venv\Scripts\activate

REM Run application
python main_gui.py

pause