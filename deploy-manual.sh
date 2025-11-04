#!/bin/bash

echo "🚀 Manual Deployment Script"
echo "==========================="

# Check if we're in the right directory
if [ ! -f "main.py" ]; then
    echo "❌ main.py not found. Please run from project root."
    exit 1
fi

# Create .env if not exists
if [ ! -f ".env" ]; then
    echo "🔧 Creating .env file from template..."
    cat > .env << EOF
# Database Configuration
DB_HOST=103.174.10.72
DB_USER=tt_crm_mcp
DB_PASSWORD=F*PAtqhu@sg2w58n
DB_NAME=tt_crm_mcp
DB_PORT=3306

# Security Configuration
MCP_API_KEYS=test-key-$(openssl rand -hex 16)

# Server Configuration
MCP_TRANSPORT=streamable-http
MCP_HOST=0.0.0.0
PORT=8080

# Optional: Debug Mode
DEBUG=False
EOF
    echo "✅ .env file created"
fi

# Install Python dependencies
echo "📦 Installing Python dependencies..."
pip install -r requirements.txt

# Start the server
echo "🚀 Starting MCP Server..."
python main.py