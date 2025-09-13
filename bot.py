import asyncio
import logging
import os
from dotenv import load_dotenv
from typing import List, Optional

import httpx
import json
import re
from bs4 import BeautifulSoup
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import UpdateType
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from settings import Settings
from registrar_clients import get_client_for_registrar, RegistrarClient

import datetime as _dt
# from functools import lru_cache

# Load .env so PAN_LIST is available via os.getenv even outside Settings
load_dotenv()

logger = logging.getLogger(__name__)


# BEGIN NEW IMPLEMENTATION ---------------------------------------------

_GROWW_CLOSED = "https://groww.in/v1/api/primaries/v1/ipo/closed"

# Map registrar URL substring to registrar code
_RTA_MAP = {
    "linkintime": "mufg",
    "mpms.mufg": "mufg",
    "in.mpms.mufg": "mufg",
    "kfintech": "kfin",
    "ipostatus.kfintech": "kfin",
    "bigshareonline": "bigshare",
    "ipo.bigshareonline": "bigshare",
    "masserv": "mas", #left
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

REGISTRAR_KEYWORDS = {
    "KFIN": "kfin",
    "KFINTECH": "kfin",
    "INTIME": "mufg",  # MUFG-Intime (Link-Intime)
    "MAS": "mas",
    "BIGSHARE": "bigshare",
}




async def _fetch_closed_ipos(session: httpx.AsyncClient) -> list[dict]:
    """Return non-SME closed IPO list with inferred registrar."""
    resp = await session.get(_GROWW_CLOSED, headers=_HEADERS)
    resp.raise_for_status()
    data = resp.json()
    # print("Groww closed IPO response snippet:", json.dumps(data, indent=2))
    result: list[dict] = []
    for item in data.get("ipoList", []):
        if item.get("isSme"):
            continue  # skip SME
        # Skip if already listed or listingPrice present or listing timestamp passed
        ts = item.get("listingTimestamp") or 0
        if item.get("listingPrice") is not None:
            continue
        import time
        now_ms = int(time.time() * 1000)
        if ts and ts < now_ms:
            continue

        name = item["companyName"].strip()
        symbol = item["symbol"]
        rta_link = (item.get("rtaLink") or "").lower()
        registrar = "mufg"  # default
        for key, val in _RTA_MAP.items():
            if key in rta_link:
                registrar = val
                break
        result.append({"name": name, "code": symbol, "registrar": registrar})
    
    # TEMP: only show Patel Retail for testing
    return result
    # [
    #     {
    #         "name": "Vikram Solar",
    #         "code": "VIKRAMSOLR",
    #         "registrar": "mufg",
    #     }
    # ]





async def fetch_ipo_catalogue() -> list[dict]:  # noqa: D401
    """Fetch latest unique mainboard IPO list from BSE & NSE."""

    async with httpx.AsyncClient(timeout=15, headers=_HEADERS) as session:
        catalogue = await _fetch_closed_ipos(session)

    return catalogue

# END NEW IMPLEMENTATION -----------------------------------------------


def build_keyboard(catalogue: List[dict]) -> InlineKeyboardMarkup:
    buttons: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    for idx, ipo in enumerate(catalogue, start=1):
        cb_data = f"ipo:{ipo['registrar']}:{ipo['name'].replace(':',' ')}"
        row.append(InlineKeyboardButton(ipo["name"], callback_data=cb_data))
        if idx % 2 == 0:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(buttons)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):  # /start or /list
    catalogue = await fetch_ipo_catalogue()
    keyboard = build_keyboard(catalogue)
    await update.message.reply_text("Select an IPO to fetch allotment status:", reply_markup=keyboard)


def get_pan_list() -> List[str]:
    pans_env = os.getenv("PAN_LIST", "").strip()
    if not pans_env:
        return []
    return [p.strip().upper() for p in pans_env.split(",") if p.strip()]


async def handle_ipo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":", 2)
    _, registrar, ipo_name = parts
    pans = get_pan_list()
    if not pans:
        await query.edit_message_text("No PANs configured. Set PAN_LIST env var.")
        return

    client: RegistrarClient = get_client_for_registrar(registrar)
    if client is None:
        await query.edit_message_text(f"Registrar '{registrar}' not supported.")
        return

    await query.edit_message_text("Fetching status, please wait…")

    async with httpx.AsyncClient(timeout=20) as session:
        tasks = [client.status_by_pan(session, pan=pan, company_name=ipo_name) for pan in pans]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    lines = [f"\n*IPO:* {ipo_name}  *(Registrar: {registrar.upper()})*\n"]
    for pan, result in zip(pans, results):
        if isinstance(result, Exception):
            logger.exception("Error fetching status", exc_info=result)
            lines.append(f"{pan}  –  error fetching status")
        else:
            lines.append(f"{pan}  –  {result}")

    text = "\n".join(lines)
    await query.edit_message_text(text=text)


async def _resolve_registrar(session: httpx.AsyncClient, scrip_cd: str, ipo_no: str, start_dt: str) -> str:
    """Fetch DisplayIPO HTML and extract registrar short code."""
    url = (
        "https://www.bseindia.com/markets/publicIssues/DisplayIPO.aspx"
        f"?id={scrip_cd}&type=IPO&idtype=1&status=L&IPONo={ipo_no}&startdt={start_dt}"
    )
    # print("IPO detail URL:", url)
    try:
        page = await session.get(url, headers=_HEADERS, timeout=10)
        page.raise_for_status()
        soup = BeautifulSoup(page.text, "html.parser")
        label = soup.find(string=re.compile(r"Registrar", re.I))
        reg_full = ""
        if label:
            link = label.find_next("a")
            if link:
                reg_full = link.get_text(strip=True).upper()
    except Exception:
        reg_full = ""

    # print("Registrar full:", reg_full)

    if "INTIME" in reg_full or "MUFG" in reg_full or "LINK" in reg_full:
        return "mufg"
    if "KFIN" in reg_full:
        return "kfin"
    if "MAS" in reg_full:
        return "mas"
    if "BIGSHARE" in reg_full:
        return "bigshare"
    return "mufg"


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help message with available commands."""
    help_text = """
📋 *Available Commands:*

/start - Show IPO list and check allotment status
/list - Show IPO list (same as /start)
/help - Show this help message
/health - Check if bot is running

*How to use:*
1. Use /start or /list to see available IPOs
2. Click on an IPO name to check allotment status
3. Results will show status for all configured PANs
    """
    await update.message.reply_text(help_text, parse_mode="Markdown")


async def setup_bot_commands(application: Application) -> None:
    """Set up bot commands for the menu button."""
    commands = [
        BotCommand("start", "Show IPO list and check allotment status"),
        BotCommand("list", "Show available IPOs"),
        BotCommand("help", "Show help message"),
        BotCommand("health", "Check if bot is running"),
    ]
    await application.bot.set_my_commands(commands)
    logger.info("Bot commands have been set up")


async def post_init(application: Application) -> None:
    """Initialize bot commands after startup."""
    await setup_bot_commands(application)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = Settings()

    application = Application.builder().token(settings.BOT_TOKEN).post_init(post_init).build()

    application.add_handler(CommandHandler("start", start))  # legacy alias
    application.add_handler(CommandHandler("list", start))   # new preferred command
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CallbackQueryHandler(handle_ipo_callback, pattern=r"^ipo:"))

    # Add a simple health check handler
    async def health_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("✅ Bot is running!")

    application.add_handler(CommandHandler("health", health_check))

    # Check if running on Render (webhook mode) or locally (polling mode)
    PORT = int(os.environ.get("PORT", 0))
    RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL")
    
    if RENDER_EXTERNAL_URL and PORT:
        # Webhook mode for Render deployment
        webhook_url = f"{RENDER_EXTERNAL_URL}/{settings.BOT_TOKEN}"
        
        logger.info(f"Starting webhook mode on port {PORT}")
        logger.info(f"Webhook URL: {webhook_url}")
        logger.info(f"Listening on 0.0.0.0:{PORT}/{settings.BOT_TOKEN}")
        
        # Run webhook with proper parameters
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=f"/{settings.BOT_TOKEN}",  # Add leading slash
            webhook_url=webhook_url,
            drop_pending_updates=False,  # Don't drop updates to see if we get any
            allowed_updates=None  # Accept all update types
        )
    else:
        # Polling mode for local development
        logger.info("Starting polling mode")
        application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
