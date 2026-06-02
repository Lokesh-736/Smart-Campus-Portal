#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
python3 -m venv venv --copies
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
echo "Done. Run: ./run.sh  or  ./venv/bin/python app.py"
