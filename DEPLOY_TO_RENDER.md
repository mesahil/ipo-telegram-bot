# Deploy IPO Telegram Bot to Render (Free)

## Prerequisites
1. A GitHub account
2. A Render account (sign up at https://render.com)
3. Your Telegram Bot Token from @BotFather

## Step 1: Push Code to GitHub
```bash
git add .
git commit -m "Add KFin IPO list extraction"
git push origin main
```

## Step 2: Deploy to Render

1. Go to https://render.com and sign in
2. Click "New +" → "Web Service"
3. Connect your GitHub repository
4. Render will automatically detect your `render.yaml` file
5. Click "Create Web Service"

## Step 3: Set Environment Variables

In Render dashboard, go to your service → Environment:

1. **BOT_TOKEN**: Your Telegram bot token from @BotFather
2. **PAN_LIST**: Comma-separated list of PANs (optional)

Example:
```
BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
PAN_LIST=ABCDE1234F,FGHIJ5678K
```

## Step 4: Keep Bot Alive (Important for Free Tier)

Render's free tier services sleep after 15 minutes of inactivity. Options:

### Option A: Use Webhook Mode (Recommended)
Update bot.py to use webhooks instead of polling:

```python
# Instead of polling
application.run_polling()

# Use webhook
application.run_webhook(
    listen="0.0.0.0",
    port=int(os.environ.get("PORT", 8443)),
    url_path=BOT_TOKEN,
    webhook_url=f"https://{YOUR_RENDER_URL}.onrender.com/{BOT_TOKEN}"
)
```

### Option B: Use External Monitoring
Use services like UptimeRobot to ping your bot every 14 minutes to keep it awake.

## Step 5: Monitor Your Bot

1. Check logs in Render dashboard → Logs
2. Test your bot on Telegram
3. Monitor for any errors

## Free Tier Limitations

- 750 hours/month (enough for 24/7 operation of one service)
- Service sleeps after 15 minutes of inactivity
- Limited to 512MB RAM
- No persistent storage (bot restarts lose state)

## Troubleshooting

1. **Bot not responding**: Check environment variables are set correctly
2. **Service sleeping**: Implement webhooks or use external monitoring
3. **Memory issues**: The bot is lightweight and should work fine on free tier

## Deploy Command
After setting up GitHub repo and Render account:
```bash
git push origin main
```
Render will auto-deploy on every push to main branch.