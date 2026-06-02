#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

if [ ! -d "venv" ] || [ ! -x "venv/bin/python" ]; then
  echo "Creating virtual environment (using --copies for Windows/OneDrive paths)..."
  python3 -m venv venv --copies
fi

echo "Installing dependencies..."
./venv/bin/pip install -q --upgrade pip
./venv/bin/pip install -q -r requirements.txt

echo "Starting Smart Campus Portal..."
exec ./venv/bin/python app.py
