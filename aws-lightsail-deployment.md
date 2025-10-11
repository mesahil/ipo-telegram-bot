# AWS Lightsail Alternative (Free for 3 Months)

## ✅ **Also FREE for 3 months!** No credit card required.

---

## 🚀 **Quick Setup**

### **Step 1: Launch Lightsail Instance**

1. Go to **Lightsail** in AWS Console
2. Click **Create instance**
3. Choose **Linux/Unix** → **OS Only** → **Amazon Linux 2**
4. Select **$3.50 USD** plan (free for 3 months)
5. Name: `ipo-telegram-bot`
6. **Create instance**

### **Step 2: Connect via Browser Terminal**

- Click **Connect** → Use browser-based SSH
- Or use your own SSH client

### **Step 3: Deploy Your Bot**

```bash
# Update and install dependencies
sudo yum update -y
sudo amazon-linux-extras install python3.11
sudo yum install git -y

# Clone and setup
git clone https://github.com/yourusername/ipo-telegram-bot.git
cd ipo-telegram-bot
pip3.11 install -r requirements.txt

# Set environment variables
export BOT_TOKEN="your_bot_token"
export PAN_LIST="your_pan_numbers"
export PORT="10000"
```

### **Step 4: Configure Static IP**

1. Go to **Networking** tab in Lightsail
2. **Create static IP** (free with Lightsail)
3. Attach to your instance

### **Step 5: Set Webhook**

```bash
curl -X POST "https://api.telegram.org/bot$BOT_TOKEN/setWebhook" \
     -d "url=https://your-static-ip:10000/$BOT_TOKEN"
```

---

## ⚖️ **EC2 vs Lightsail Comparison**

| Feature              | EC2 t2.micro | Lightsail $3.50 |
| -------------------- | ------------ | --------------- |
| **Free Period**      | 12 months    | 3 months        |
| **Setup Complexity** | Medium       | Easy            |
| **Static IP**        | Extra step   | Built-in        |
| **Monitoring**       | Advanced     | Simple          |
| **Scaling**          | Flexible     | Limited         |

## 🎯 **Recommendation**

- **EC2** for long-term (12 months free)
- **Lightsail** for quick start (3 months free, easier setup)

Both options are completely free initially and perfect for your Telegram bot! 🚀
