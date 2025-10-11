# AWS EC2 Free Tier Deployment Guide

## ✅ **YES, this is completely FREE for 12 months!**

AWS provides **t2.micro** instances free for 12 months under their Free Tier. No credit card required if you stay within free tier limits.

---

## 🚀 **Deployment Steps**

### **Step 1: Create AWS Account**

1. Go to [aws.amazon.com](https://aws.amazon.com)
2. Click "Create an AWS Account"
3. Use your email and phone number
4. **Important:** Select "Basic Support - Free" plan
5. No credit card required for free tier

### **Step 2: Launch EC2 Instance**

1. Go to **EC2 Dashboard** → **Launch Instance**
2. Choose **Amazon Linux 2 AMI (HVM)**
3. Select **t2.micro** (free tier eligible)
4. Configure security group:
   - Add **SSH** (port 22) - for you to connect
   - Add **Custom TCP** port **10000** (or your PORT) - for webhooks
5. **Launch** the instance

### **Step 3: Connect to Instance**

```bash
# From your terminal
ssh -i your-key.pem ec2-user@your-instance-ip

# Update system
sudo yum update -y

# Install Python 3.11
sudo amazon-linux-extras install python3.11

# Install pip
curl -O https://bootstrap.pypa.io/get-pip.py
python3.11 get-pip.py

# Install git
sudo yum install git -y
```

### **Step 4: Deploy Your Bot**

```bash
# Clone your repository
git clone https://github.com/yourusername/ipo-telegram-bot.git
cd ipo-telegram-bot

# Install dependencies
pip3.11 install -r requirements.txt

# Set environment variables
export BOT_TOKEN="your_bot_token"
export PAN_LIST="your_pan_numbers"
export PORT="10000"

# Test locally first
python3.11 bot.py &
```

### **Step 5: Configure Webhook Mode**

Update your bot code to use webhook mode for production:

```python
# In bot.py, ensure this section is active:
if RENDER_EXTERNAL_URL and PORT:
    # Webhook mode for production
    webhook_url = f"{RENDER_EXTERNAL_URL}/{settings.BOT_TOKEN}"

    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=f"/{settings.BOT_TOKEN}",
        webhook_url=webhook_url,
    )
```

### **Step 6: Set Webhook URL**

After deployment, set your webhook:

```bash
curl -X POST "https://api.telegram.org/bot$BOT_TOKEN/setWebhook" \
     -d "url=https://your-ec2-ip:10000/$BOT_TOKEN"
```

---

## 📊 **Free Tier Limits**

- **t2.micro**: 750 hours/month (free for 12 months)
- **Elastic IP**: 1 free (for static IP)
- **Data Transfer**: 100GB/month out (free)
- **Storage**: 30GB EBS (free)

## 🔧 **Maintenance**

```bash
# Check if bot is running
ps aux | grep python

# View logs
tail -f /var/log/messages

# Restart bot if needed
pkill python && python3.11 bot.py &
```

---

## ⚠️ **Important Notes**

1. **Security Group**: Only open necessary ports
2. **Environment Variables**: Never commit secrets to git
3. **Monitoring**: Check AWS console for usage
4. **Auto-start**: Consider using systemd for production
5. **Updates**: Keep your instance updated regularly

## 🛡️ **Security Checklist**

- [ ] Use strong SSH key
- [ ] Configure firewall properly
- [ ] Use environment variables for secrets
- [ ] Enable AWS CloudWatch monitoring
- [ ] Set up proper logging

---

## 💰 **Cost After Free Tier**

- t2.micro: ~$8/month
- Data transfer: ~$0.09/GB over 100GB
- Very affordable for a personal project!

This deployment method will work perfectly for your Telegram bot and stays completely within AWS Free Tier limits! 🎉
