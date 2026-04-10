#!/bin/bash

# cd "$(dirname "$0")" || exit

# # Activate environment
# source venv/bin/activate

# # Run application
# python3 -m sd_viewer.main_gui

# echo
# echo "Press any key to exit..."
# read



SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.."

source venv/bin/activate

python3 sd_viewer/main_gui.py

echo
read -p "Press Enter to exit..."