#!/bin/bash
set -e

VENV_DIR=".venv"

if [ ! -d "$VENV_DIR" ]; then
  echo "Creating virtual environment in $VENV_DIR..."
  python3 -m venv "$VENV_DIR"
else
  echo "Virtual environment already exists."
fi

echo "Installing dependencies from requirements.txt..."
"$VENV_DIR/bin/pip" install -r requirements.txt

echo ""
echo "Setup complete. To activate the virtual environment, run:"
echo "source $VENV_DIR/bin/activate"
