#!/bin/bash

cd "$(dirname "$0")" || exit

echo "Setting up environment..."

# Check for Python 3
if ! command -v python3 &> /dev/null
then
    echo "Python 3 is not installed. Please install Python 3 and try again."
    read
    exit
fi

# Create virtual environment
python3 -m venv venv

# Activate environment
source venv/bin/activate

# Upgrade pip and install dependencies
pip install --upgrade pip
pip install -r requirements.txt

echo
echo "Setup complete."
echo "Double-click run_viewer.command to start the application."
read