import asyncio
import logging
import os
from typing import List, Optional

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from settings import Settings
from registrar_clients import get_client_for_registrar, RegistrarClient

import datetime as _dt
import re
from functools import lru_cache

logger = logging.getLogger(__name__)


# BEGIN NEW IMPLEMENTATION ---------------------------------------------

_BSE_API = "https://api.bseindia.com/BseIndiaAPI/api/GetPublicIssue_par/w"
_NSE_URL = "https://www.nseindia.com/api/ipo-current-issue"
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


def _infer_registrar(text: str) -> str:
    """Guess registrar short-code from free-form text."""
    upper = text.upper()
    for key, val in REGISTRAR_KEYWORDS.items():
        if key in upper:
            return val
    return "mufg"  # safe default – Link-Intime handles majority


async def _fetch_bse(session: httpx.AsyncClient) -> list[dict]:
    """Return list of IPO dicts from new BSE JSON endpoint (show all)."""
    resp = await session.get(_BSE_API, headers={**_HEADERS, "Referer": "https://www.bseindia.com/"})
    resp.raise_for_status()
    data = resp.json()

    result: list[dict] = []
    for item in data.get("Table", []):
        if item.get("eXCHANGE_PLATFORM", "").lower() != "mainboard":
            continue  # only mainboard issues
        if item.get("IR_flag", "").upper() != "IPO":
            continue  # only IPO, exclude FPO/RI etc.
        name = str(item.get("Scrip_Name", "")).strip().title()
        if not name:
            continue
        code = str(item.get("Scrip_cd", item.get("IPO_NO", name))).strip()
        registrar = _infer_registrar(name)
        result.append({"code": code, "name": name, "registrar": registrar})
    return result


async def _fetch_nse(session: Optional[httpx.AsyncClient] = None) -> list[dict]:
    """Fetch NSE IPO JSON directly without prior homepage visit."""
    close_client = False
    if session is None:
        session = httpx.AsyncClient(timeout=10, headers=_HEADERS)
        close_client = True
    try:
        # Hit NSE homepage to grab cookies (bypasses 401 on API).
        if session:
            await session.get("https://www.nseindia.com", headers=_HEADERS)
        api_headers = {
            **_HEADERS,
            "Referer": "https://www.nseindia.com/",
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        }
        resp = await session.get(_NSE_URL, headers=api_headers)
        if resp.status_code == 401:
            # Sometimes cookies take a moment; retry once after short sleep.
            await asyncio.sleep(1)
            resp = await session.get(_NSE_URL, headers=api_headers)
        print("NSE direct response (first 200):", resp.text[:200])
        resp.raise_for_status()
    finally:
        if close_client:
            await session.aclose()

    # If JSON parsing fails, return empty list.
    try:
        data = resp.json()
    except Exception:
        return []

    items = data if isinstance(data, list) else data.get("data", [])
    result: list[dict] = []
    for item in items:
        if item.get("series", "").upper() != "EQ":
            continue  # keep only main equity series
        name = str(item.get("companyName") or item.get("issuerName") or "").strip()
        if not name:
            continue
        code = str(item.get("symbol") or item.get("securityCode") or name.split()[0]).strip().upper()
        registrar = _infer_registrar(name)
        result.append({"code": code, "name": name, "registrar": registrar})
    return result


@lru_cache(maxsize=1)
async def fetch_ipo_catalogue() -> list[dict]:  # noqa: D401
    """Fetch latest unique mainboard IPO list from BSE & NSE."""

    async with httpx.AsyncClient(timeout=15, headers=_HEADERS, http2=True) as session:
        nse_list = await _fetch_nse(session)
        bse_list = await _fetch_bse(session)

    combined: list[dict] = []
    seen_names: set[str] = set()

    for source in (bse_list, nse_list):
        if isinstance(source, Exception):
            logger.warning("Could not fetch IPO list from one exchange: %s", source)
            continue
        for ipo in source:
            key = ipo["name"].upper()
            if key not in seen_names:
                seen_names.add(key)
                combined.append(ipo)

    return combined

# END NEW IMPLEMENTATION -----------------------------------------------


def build_keyboard(catalogue: List[dict]) -> InlineKeyboardMarkup:
    buttons: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    for idx, ipo in enumerate(catalogue, start=1):
        row.append(
            InlineKeyboardButton(ipo["name"], callback_data=f"ipo:{ipo['registrar']}:{ipo['code']}")
        )
        if idx % 2 == 0:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(buttons)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    _, registrar, code = query.data.split(":", 2)
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
        tasks = [client.status_by_pan(session, pan=pan, ipo_code=code) for pan in pans]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    lines = [f"\n*IPO:* {code}  *(Registrar: {registrar.upper()})*\n"]
    for pan, result in zip(pans, results):
        if isinstance(result, Exception):
            logger.exception("Error fetching status", exc_info=result)
            lines.append(f"`{pan}`  –  _error fetching status_")
        else:
            lines.append(f"`{pan}`  –  {result}")

    text = "\n".join(lines)
    await query.edit_message_text(text=text, parse_mode="Markdown")


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = Settings()

    application = Application.builder().token(settings.BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(handle_ipo_callback, pattern=r"^ipo:"))

    application.run_polling()


if __name__ == "__main__":
    main()
