# IPO Allotment Status Telegram Bot

A zero-cost Telegram bot that shows allotment status for a list of pre-defined PANs across multiple Indian IPO registrars (MUFG Intime, MAS Services, etc.).

## Quick start (local)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export BOT_TOKEN="<your_botfather_token>"
export PAN_LIST="ABCDE1234F,XYZAB1234G"
python bot.py
```

## 🚀 Deployment Options (All FREE!)

### Option 1: AWS EC2 (Free for 12 months)

✅ **Completely free for 12 months** - No credit card required!

See [aws-ec2-deployment.md](aws-ec2-deployment.md) for detailed instructions.

**Quick setup:**

1. Create AWS account (no card needed for free tier)
2. Launch t2.micro instance (free for 12 months)
3. Run the deployment script: `./deploy-to-aws.sh`

### Option 2: AWS Lightsail (Free for 3 months)

✅ **Free for 3 months** - Easier setup than EC2!

See [aws-lightsail-deployment.md](aws-lightsail-deployment.md) for quick setup guide.

### Option 3: Render (Free tier)

1. Push this repo to GitHub.
2. In Render → **New → Web Service** → pick the repo.
3. Add two environment variables:
   - `BOT_TOKEN` – the token from BotFather.
   - `PAN_LIST` – comma-separated PAN list.
4. Agree to the free plan and deploy. The bot runs using long-polling.

## Configuration

| Variable    | Purpose                                |
| ----------- | -------------------------------------- |
| BOT_TOKEN   | Telegram bot token                     |
| PAN_LIST    | Comma-separated list of PANs to query  |
| PORT        | Port for webhook mode (default: 10000) |
| WEBHOOK_URL | Full webhook URL for AWS deployment    |

## Folder layout

```
bot.py              # entry point
settings.py         # env config
registrar_clients/  # one file per registrar
render.yaml         # Render service definition
```

Feel free to extend the `registrar_clients/` modules to support additional registrars.
