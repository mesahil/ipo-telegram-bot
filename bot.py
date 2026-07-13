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
from datetime import datetime
# from functools import lru_cache

# Load .env so PAN_LIST is available via os.getenv even outside Settings
load_dotenv()

logger = logging.getLogger(__name__)

# Store scheduled jobs per user
scheduled_jobs = {}


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
    # return [
    #     {
    #         "name": "DEV ACCELERATOR LIMITED",
    #         "code": "DEVX",
    #         "registrar": "kfin",
    #     }
    # ]





async def fetch_ipo_catalogue() -> list[dict]:  # noqa: D401
    """Fetch latest unique mainboard IPO list from BSE & NSE."""

    async with httpx.AsyncClient(timeout=15, headers=_HEADERS) as session:
        catalogue = await _fetch_closed_ipos(session)

    return catalogue

# END NEW IMPLEMENTATION -----------------------------------------------


async def fetch_ipo_market_data() -> List[dict]:
    """Fetch IPO market data including GMP from InvestorGain API."""
    # Build dynamic URL
    url = "https://webnodejs.investorgain.com/cloud/v2/index/gmp-price-read"
    logger.info(f"Fetching market data from: {url}")

    async with httpx.AsyncClient(timeout=15, headers=_HEADERS) as session:
        try:
            resp = await session.get(url)
            print("Response:", resp.text)
            resp.raise_for_status()
            data = resp.json()

            # Extract gmpList
            gmp_list = data.get("gmpList", [])

            # Process and format the data
            processed_data = []
            for item in gmp_list:
                name = item.get("company_short_name", "").strip()
                if not name:
                    continue

                gmp_val = item.get("gmp", "0")
                gmp_perc = item.get("gmp_perc", "0")
                gmp_display = f"{gmp_val} ({gmp_perc}%)"

                processed_data.append({
                    "name": name,
                    "gmp": gmp_display,
                    "open": "NA",
                    "close": "NA",
                    "boa_date": "NA",
                    "listing": "NA",
                })

            logger.info(f"Processed {len(processed_data)} IPOs")
            return processed_data
        except Exception as e:
            logger.error(f"Error fetching IPO market data: {e}")
            logger.error(f"URL was: {url}")
            return []


async def format_and_send_market_data(context, chat_id, market_data):
    """Format and send market data to a chat."""
    if not market_data:
        await context.bot.send_message(chat_id=chat_id, text="Unable to fetch market data at the moment.")
        return

    # Format the response
    response_lines = ["📊 *IPO Market Data*\n"]

    for ipo in market_data:
        lines = [
            f"\n*{ipo['name']}*",
            f"📈 GMP: ₹{ipo['gmp']}",
            f"📅 Open: {ipo['open']}",
            f"📅 Close: {ipo['close']}",
            f"📋 Allotment: {ipo['boa_date']}",
            f"🔔 Listing: {ipo['listing']}",
            "─" * 20
        ]
        response_lines.extend(lines)

    # Send in chunks if message is too long
    message = "\n".join(response_lines)
    if len(message) > 4000:
        # Split into multiple messages
        chunks = []
        current_chunk = []
        current_length = 0

        for line in response_lines:
            if current_length + len(line) > 3500:
                chunks.append("\n".join(current_chunk))
                current_chunk = [line]
                current_length = len(line)
            else:
                current_chunk.append(line)
                current_length += len(line)

        if current_chunk:
            chunks.append("\n".join(current_chunk))

        for chunk in chunks:
            await context.bot.send_message(chat_id=chat_id, text=chunk, parse_mode="Markdown")
    else:
        await context.bot.send_message(chat_id=chat_id, text=message, parse_mode="Markdown")


async def market_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show IPO market data including GMP - one time."""
    await update.message.reply_text("Fetching market data...")
    market_data = await fetch_ipo_market_data()
    await format_and_send_market_data(context, update.effective_chat.id, market_data)


async def scheduled_market_update(context: ContextTypes.DEFAULT_TYPE):
    """Job callback for scheduled market updates."""
    chat_id = context.job.chat_id
    market_data = await fetch_ipo_market_data()
    await format_and_send_market_data(context, chat_id, market_data)


async def start_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start daily market updates at 9 AM."""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    # Check if already scheduled
    if user_id in scheduled_jobs:
        await update.message.reply_text("⚠️ Daily updates are already scheduled for 9:00 AM.")
        return

    # Schedule daily job at 9:00 AM IST
    # Note: The bot uses UTC, so 9:00 AM IST = 3:30 AM UTC
    import pytz
    from datetime import time

    ist = pytz.timezone('Asia/Kolkata')
    job = context.job_queue.run_daily(
        scheduled_market_update,
        time=time(hour=3, minute=30),  # 3:30 AM UTC = 9:00 AM IST
        chat_id=chat_id,
        name=str(user_id)
    )

    scheduled_jobs[user_id] = job
    await update.message.reply_text("✅ Daily IPO market updates scheduled for 9:00 AM IST.")


async def stop_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stop daily market updates."""
    user_id = update.effective_user.id

    if user_id not in scheduled_jobs:
        await update.message.reply_text("⚠️ No scheduled updates found.")
        return

    job = scheduled_jobs[user_id]
    job.schedule_removal()
    del scheduled_jobs[user_id]

    await update.message.reply_text("✅ Daily updates have been stopped.")


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
    
    # Handle fuzzy match confirmations
    if query.data.startswith("fuzz_"):
        from confirmation_handler import confirmation_handler
        handled = await confirmation_handler.handle_confirmation_response(update, context)
        if handled:
            return
    
    # Original IPO callback handling
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
        # Try a quick test with first PAN to see if company exists
        try:
            test_result = await client.status_by_pan(session, pan=pans[0], company_name=ipo_name)
            
            # Check if we got "not available" response and client supports fuzzy matching
            if ("not yet available" in test_result.lower() or "not available" in test_result.lower()) and hasattr(client, 'find_fuzzy_matches'):
                # Try fuzzy matching
                fuzzy_matches = await client.find_fuzzy_matches(session, ipo_name)
                
                if fuzzy_matches:
                    # Request user confirmation for fuzzy matches
                    from confirmation_handler import confirmation_handler
                    
                    # Store PAN list in context for later use
                    context.user_data['fuzzy_pans'] = pans
                    context.user_data['fuzzy_registrar'] = registrar
                    
                    await confirmation_handler.request_confirmation(
                        update, context, ipo_name, fuzzy_matches, registrar, pans[0]
                    )
                    return
        except Exception as e:
            # If there's an error with the test, try fuzzy matching as fallback
            if hasattr(client, 'find_fuzzy_matches'):
                try:
                    fuzzy_matches = await client.find_fuzzy_matches(session, ipo_name)
                    
                    if fuzzy_matches:
                        from confirmation_handler import confirmation_handler
                        
                        context.user_data['fuzzy_pans'] = pans
                        context.user_data['fuzzy_registrar'] = registrar
                        
                        await confirmation_handler.request_confirmation(
                            update, context, ipo_name, fuzzy_matches, registrar, pans[0]
                        )
                        return
                except Exception:
                    pass  # Continue to normal processing
        
        # Normal processing for all PANs (either company found or fuzzy not available/not needed)
        tasks = [client.status_by_pan(session, pan=pan, company_name=ipo_name) for pan in pans]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    # Format and send results for all PANs
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

*IPO Services:*
/closed_ipo - Show closed IPOs and check allotment status
/all_active_ipo - Show all active IPOs with GMP data


*Other:*
/health - Check if bot is running
/help - Show this help message

*How to use:*
1. Use /closed_ipo to see closed IPOs and check allotment status
2. Click on an IPO name to check your allotment status
3. Use /all_active_ipo to see active IPOs with GMP data
4. Legacy commands /list and /market still work
    """
    await update.message.reply_text(help_text, parse_mode="Markdown")


async def setup_bot_commands(application: Application) -> None:
    """Set up bot commands for the menu button."""
    commands = [
        BotCommand("menu", "--- BOT MENU ---"),
        BotCommand("health", "Check if bot is running"),
        BotCommand("closed_ipo", "Show closed IPOs and check allotment status"),
        BotCommand("all_active_ipo", "Show all active IPOs with GMP data"),
        # BotCommand("start_schedule", "Start daily updates at 9 AM IST"),
        # BotCommand("stop_schedule", "Stop daily updates"),
        # BotCommand("help", "Show help message"),
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

    application.add_handler(CommandHandler("menu", help_command))
    application.add_handler(CommandHandler("start", start))  # legacy alias
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("closed_ipo", start))  # legacy alias
    application.add_handler(CommandHandler("all_active_ipo", market_command))  # new command for active IPOs
    # application.add_handler(CommandHandler("start_schedule", start_schedule))
    # application.add_handler(CommandHandler("stop_schedule", stop_schedule))
    application.add_handler(CallbackQueryHandler(handle_ipo_callback, pattern=r"^(ipo:|fuzz_)"))

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
