#!/bin/bash
# Quick run script - activates venv and runs the app

set -e

if [ ! -d "venv" ] && [ ! -d ".venv" ]; then
    echo "❌ Virtual environment not found. Run ./setup.sh first"
    exit 1
fi

# Activate venv
if [ -d "venv" ]; then
    source venv/bin/activate
elif [ -d ".venv" ]; then
    source .venv/bin/activate
fi

echo "🚀 Starting Mostaql Job Notifier..."
echo "📍 API will be available at: http://localhost:8000"
echo "📍 API docs at: http://localhost:8000/docs"
echo ""

# Run the application
python -m backend.main

