#!/bin/bash

cd "$(dirname "$0")" || exit

# Activate environment
source venv/bin/activate

# Run application
python3 main_gui.py

echo
echo "Press any key to exit..."
read