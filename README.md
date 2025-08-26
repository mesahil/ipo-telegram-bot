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

## Deployment on Render (free)

1. Push this repo to GitHub.
2. In Render → **New → Web Service** → pick the repo.
3. Add two environment variables:
   * `BOT_TOKEN` – the token from BotFather.
   * `PAN_LIST`  – comma-separated PAN list.
4. Agree to the free plan and deploy. The bot runs using long-polling.

## Configuration

| Variable   | Purpose                                   |
|------------|-------------------------------------------|
| BOT_TOKEN  | Telegram bot token                        |
| PAN_LIST   | Comma-separated list of PANs to query     |

## Folder layout

```
bot.py              # entry point
settings.py         # env config
registrar_clients/  # one file per registrar
render.yaml         # Render service definition
```

Feel free to extend the `registrar_clients/` modules to support additional registrars.
