#!/bin/bash
# Setup script for Mostaql Job Notifier

set -e

echo "🚀 Setting up Mostaql Job Notifier..."
echo ""

# Check Python version
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is not installed. Please install Python 3.11 or higher."
    exit 1
fi

PYTHON_VERSION=$(python3 --version | cut -d' ' -f2 | cut -d'.' -f1,2)
echo "✓ Found Python $PYTHON_VERSION"

# Create virtual environment
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
    echo "✓ Virtual environment created"
else
    echo "✓ Virtual environment already exists"
fi

# Activate virtual environment
echo "🔌 Activating virtual environment..."
source venv/bin/activate

# Upgrade pip
echo "⬆️  Upgrading pip..."
pip install --upgrade pip

# Install dependencies
echo "📥 Installing dependencies..."
pip install -r requirements.txt

# Generate secret key if needed
if grep -q "CHANGE_THIS_TO_RANDOM_STRING" .env; then
    echo "🔑 Generating SECRET_KEY..."
    SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
    sed -i "s/SECRET_KEY=.*/SECRET_KEY=$SECRET_KEY/" .env
    echo "✓ SECRET_KEY generated"
fi

# Create necessary directories
echo "📁 Creating directories..."
mkdir -p data logs

# Initialize database
echo "🗄️  Initializing database..."
python -m backend.database

echo ""
echo "✅ Setup complete!"
echo ""
echo "To run the application:"
echo "  ./run.sh"
echo ""

