#!/bin/bash
echo "=========================================="
echo " LittleAnt V14"
echo "=========================================="
python3 --version 2>/dev/null || { echo "❌ Python 3.10+ required"; exit 1; }
echo "✅ Python ready (zero dependencies)"

CONFIG="$(dirname "$0")/littleant/config.json"
if [ ! -f "$CONFIG" ]; then
    echo ""
    echo "⚠️  No config found. Running setup..."
    echo ""
    python3 "$(dirname "$0")/setup.py"
fi

echo ""
echo "Cleaning old processes..."
pkill -f "run.py" 2>/dev/null
sleep 2
echo "Starting Telegram Bot..."
echo "Press Ctrl+C to stop"
echo ""
cd "$(dirname "$0")"
python3 run.py
