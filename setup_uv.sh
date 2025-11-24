#!/bin/bash
# Setup script using uv (faster alternative to pip)
# Install uv first: curl -LsSf https://astral.sh/uv/install.sh | sh

set -e

echo "🚀 Setting up Mostaql Job Notifier with uv..."
echo ""

# Check if uv is installed
if ! command -v uv &> /dev/null; then
    echo "❌ uv is not installed."
    echo "Install it with: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

echo "✓ Found uv"

# Create virtual environment with uv
if [ ! -d ".venv" ]; then
    echo "📦 Creating virtual environment with uv..."
    uv venv
    echo "✓ Virtual environment created"
else
    echo "✓ Virtual environment already exists"
fi

# Activate virtual environment
echo "🔌 Activating virtual environment..."
source .venv/bin/activate

# Install dependencies with uv (much faster than pip)
echo "📥 Installing dependencies with uv..."
uv pip install -r requirements.txt

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

