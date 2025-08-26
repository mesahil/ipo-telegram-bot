import asyncio
import logging
import os
from dotenv import load_dotenv
from typing import List, Optional

import httpx
import json
import re
from bs4 import BeautifulSoup
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from settings import Settings
from registrar_clients import get_client_for_registrar, RegistrarClient

import datetime as _dt
# from functools import lru_cache

# Load .env so PAN_LIST is available via os.getenv even outside Settings
load_dotenv()

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




async def _fetch_bse(session: httpx.AsyncClient) -> list[dict]:
    """Return list of IPO dicts from new BSE JSON endpoint (show all)."""
    resp = await session.get(_BSE_API, headers={**_HEADERS, "Referer": "https://www.bseindia.com/"})
    resp.raise_for_status()
    data = resp.json()
    # print("BSE direct response snippet:", json.dumps(data, indent=2))

    result: list[dict] = []
    for item in data.get("Table", []):
        if item.get("eXCHANGE_PLATFORM", "").lower() != "mainboard":
            continue  # only mainboard issues
        if item.get("IR_flag", "").upper() != "IPO":
            continue  # only IPO, exclude FPO/RI etc.
        name = str(item.get("Scrip_Name", "")).strip().title()
        if not name:
            continue
        scrip_cd = str(item.get("Scrip_cd")).strip()
        ipo_no = str(item.get("IPO_NO")).strip()
        start_dt_raw = item.get("Start_Dt", "")
        start_dt = ""
        if start_dt_raw:
            # convert 2025-08-26T00:00:00 -> 26/08/2025
            start_dt = start_dt_raw.split("T")[0]
            y, m, d = start_dt.split("-")
            start_dt = f"{d}/{m}/{y}"
        result.append({"code": scrip_cd, "ipo_no": ipo_no, "start": start_dt, "name": name})
    return result


# async def _fetch_nse(session: Optional[httpx.AsyncClient] = None) -> list[dict]:
#     """Fetch NSE IPO JSON directly without prior homepage visit."""
#     close_client = False
#     if session is None:
#         session = httpx.AsyncClient(timeout=10, headers=_HEADERS)
#         close_client = True
#     try:
#         # Hit NSE homepage to grab cookies (bypasses 401 on API).
#         if session:
#             await session.get("https://www.nseindia.com", headers=_HEADERS)
#         api_headers = {
#             **_HEADERS,
#             "Referer": "https://www.nseindia.com/",
#             "Accept": "application/json",
#             "X-Requested-With": "XMLHttpRequest",
#         }
#         resp = await session.get(_NSE_URL, headers=api_headers)
#         if resp.status_code == 401:
#             # Sometimes cookies take a moment; retry once after short sleep.
#             await asyncio.sleep(1)
#             resp = await session.get(_NSE_URL, headers=api_headers)
#         print("NSE direct response (first 200):", resp.text[:200])
#         resp.raise_for_status()
#     finally:
#         if close_client:
#             await session.aclose()

#     # If JSON parsing fails, return empty list.
#     try:
#         data = resp.json()
#     except Exception:
#         return []

#     items = data if isinstance(data, list) else data.get("data", [])
#     result: list[dict] = []
#     for item in items:
#         if item.get("series", "").upper() != "EQ":
#             continue  # keep only main equity series
#         name = str(item.get("companyName") or item.get("issuerName") or "").strip()
#         if not name:
#             continue
#         code = str(item.get("symbol") or item.get("securityCode") or name.split()[0]).strip().upper()
#         result.append({"code": code, "name": name})
#     return result


async def fetch_ipo_catalogue() -> list[dict]:  # noqa: D401
    """Fetch latest unique mainboard IPO list from BSE & NSE."""

    async with httpx.AsyncClient(timeout=15, headers=_HEADERS, http2=True) as session:
        nse_list = []  # NSE temporarily disabled
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
        cb_data = (
            f"ipo:{ipo['code']}:{ipo.get('ipo_no','')}:{ipo.get('start','')}:"
            f"{ipo['name'].replace(':', ' ')}"
        )
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
    parts = query.data.split(":", 4)
    _, scrip_cd, ipo_no, start_dt, ipo_name = parts
    pans = get_pan_list()
    if not pans:
        await query.edit_message_text("No PANs configured. Set PAN_LIST env var.")
        return

    async with httpx.AsyncClient(timeout=15, headers=_HEADERS, http2=True) as resolver:
        registrar = await _resolve_registrar(resolver, scrip_cd, ipo_no, start_dt)

    client: RegistrarClient = get_client_for_registrar(registrar)
    if client is None:
        await query.edit_message_text(f"Registrar '{registrar}' not supported.")
        return

    await query.edit_message_text("Fetching status, please wait…")

    async with httpx.AsyncClient(timeout=20) as session:
        tasks = [client.status_by_pan(session, pan=pan, ipo_code=scrip_cd) for pan in pans]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    lines = [f"\n*IPO:* {ipo_name}  *(Registrar: {registrar.upper()})*\n"]
    for pan, result in zip(pans, results):
        if isinstance(result, Exception):
            logger.exception("Error fetching status", exc_info=result)
            lines.append(f"`{pan}`  –  _error fetching status_")
        else:
            lines.append(f"`{pan}`  –  {result}")

    text = "\n".join(lines)
    await query.edit_message_text(text=text, parse_mode="Markdown")


async def _resolve_registrar(session: httpx.AsyncClient, scrip_cd: str, ipo_no: str, start_dt: str) -> str:
    """Fetch DisplayIPO HTML and extract registrar short code."""
    url = (
        "https://www.bseindia.com/markets/publicIssues/DisplayIPO.aspx"
        f"?id={scrip_cd}&type=IPO&idtype=1&status=L&IPONo={ipo_no}&startdt={start_dt}"
    )
    print("IPO detail URL:", url)
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

    print("Registrar full:", reg_full)

    if "INTIME" in reg_full or "MUFG" in reg_full or "LINK" in reg_full:
        return "mufg"
    if "KFIN" in reg_full:
        return "kfin"
    if "MAS" in reg_full:
        return "mas"
    if "BIGSHARE" in reg_full:
        return "bigshare"
    return "mufg"


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = Settings()

    application = Application.builder().token(settings.BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))  # legacy alias
    application.add_handler(CommandHandler("list", start))   # new preferred command
    application.add_handler(CallbackQueryHandler(handle_ipo_callback, pattern=r"^ipo:"))

    application.run_polling()


if __name__ == "__main__":
    main()
