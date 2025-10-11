#!/bin/bash

# AWS EC2 Deployment Script for IPO Telegram Bot
# Run this script on your AWS EC2 instance

echo "🚀 Deploying IPO Telegram Bot to AWS EC2..."

# Update system
echo "📦 Updating system packages..."
sudo yum update -y

# Install Python 3.11
echo "🐍 Installing Python 3.11..."
sudo amazon-linux-extras install python3.11 -y

# Install pip
echo "📦 Installing pip..."
curl -O https://bootstrap.pypa.io/get-pip.py
python3.11 get-pip.py
rm get-pip.py

# Install git
echo "📥 Installing git..."
sudo yum install git -y

# Clone repository (replace with your repo URL)
echo "🔗 Cloning repository..."
git clone https://github.com/yourusername/ipo-telegram-bot.git
cd ipo-telegram-bot

# Install dependencies
echo "📚 Installing Python dependencies..."
pip3.11 install -r requirements.txt

# Set environment variables (replace with your actual values)
echo "⚙️ Configuring environment..."
export BOT_TOKEN="YOUR_BOT_TOKEN_HERE"
export PAN_LIST="YOUR_PAN_NUMBERS_HERE"
export PORT="10000"
export WEBHOOK_URL="https://your-ec2-ip:10000"

# Create systemd service for auto-start
echo "🔧 Creating systemd service..."
sudo tee /etc/systemd/system/ipo-bot.service > /dev/null <<EOF
[Unit]
Description=IPO Telegram Bot
After=network.target

[Service]
Type=simple
User=ec2-user
WorkingDirectory=/home/ec2-user/ipo-telegram-bot
Environment=BOT_TOKEN=${BOT_TOKEN}
Environment=PAN_LIST=${PAN_LIST}
Environment=PORT=${PORT}
Environment=WEBHOOK_URL=${WEBHOOK_URL}
ExecStart=/usr/bin/python3.11 /home/ec2-user/ipo-telegram-bot/bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Enable and start service
echo "🚀 Starting bot service..."
sudo systemctl daemon-reload
sudo systemctl enable ipo-bot.service
sudo systemctl start ipo-bot.service

# Check status
echo "✅ Checking service status..."
sudo systemctl status ipo-bot.service --no-pager -l

echo ""
echo "🎉 Deployment complete!"
echo ""
echo "📋 Next steps:"
echo "1. Get your EC2 instance IP from AWS console"
echo "2. Update WEBHOOK_URL above with: https://your-ec2-ip:10000"
echo "3. Set Telegram webhook:"
echo "   curl -X POST \"https://api.telegram.org/bot\$BOT_TOKEN/setWebhook\" \\"
echo "        -d \"url=https://your-ec2-ip:10000/\$BOT_TOKEN\""
echo ""
echo "🔍 Monitor logs: sudo journalctl -u ipo-bot.service -f"
echo "🛑 Stop service: sudo systemctl stop ipo-bot.service"
