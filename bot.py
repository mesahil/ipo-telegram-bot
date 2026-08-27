import asyncio
import logging
import os
from dotenv import load_dotenv
from typing import List, Optional, Union

import httpx
import json
import re
import tornado.web
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

JSONBIN_API_KEY = os.getenv("JSONBIN_API_KEY")
JSONBIN_BIN_ID = os.getenv("JSONBIN_BIN_ID")
SUBSCRIBERS_FILE = "subscribers.json"

_DATA_CACHE: Optional[dict] = None
_CACHE_TIMESTAMP: float = 0.0
_CACHE_TTL_SECONDS: float = 60.0


def get_jsonbin_data(force_refresh: bool = False) -> dict:
    global _DATA_CACHE, _CACHE_TIMESTAMP
    import time
    now = time.time()

    # Fast in-memory cache check to prevent repetitive HTTP GET calls to JSONBin
    if not force_refresh and _DATA_CACHE is not None and (now - _CACHE_TIMESTAMP) < _CACHE_TTL_SECONDS:
        return _DATA_CACHE

    default_data = {"subscribers": [], "allotment_subscriptions": [], "auto_subscribers": []}
    data = None

    if JSONBIN_API_KEY and JSONBIN_BIN_ID:
        try:
            url = f"https://api.jsonbin.io/v3/b/{JSONBIN_BIN_ID}/latest"
            headers = {"X-Master-Key": JSONBIN_API_KEY}
            resp = httpx.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                record = resp.json().get("record", {})
                if isinstance(record, list):
                    data = {"subscribers": record, "allotment_subscriptions": [], "auto_subscribers": []}
                elif isinstance(record, dict):
                    data = {
                        "subscribers": record.get("subscribers", []),
                        "allotment_subscriptions": record.get("allotment_subscriptions", []),
                        "auto_subscribers": record.get("auto_subscribers", [])
                    }
                else:
                    data = default_data
                try:
                    with open(SUBSCRIBERS_FILE, "w") as f:
                        json.dump(data, f)
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"[JSONBIN ERROR] Failed reading data from Jsonbin.io: {e}")

    if data is None:
        if os.path.exists(SUBSCRIBERS_FILE):
            try:
                with open(SUBSCRIBERS_FILE, "r") as f:
                    content = json.load(f)
                    if isinstance(content, list):
                        data = {"subscribers": content, "allotment_subscriptions": [], "auto_subscribers": []}
                    elif isinstance(content, dict):
                        data = {
                            "subscribers": content.get("subscribers", []),
                            "allotment_subscriptions": content.get("allotment_subscriptions", []),
                            "auto_subscribers": content.get("auto_subscribers", [])
                        }
                    else:
                        data = default_data
            except Exception as e:
                logger.error(f"Error reading subscribers file: {e}")
                data = default_data
        else:
            data = default_data

    _DATA_CACHE = data
    _CACHE_TIMESTAMP = now
    return data


def _save_jsonbin_data(data: dict) -> bool:
    global _DATA_CACHE, _CACHE_TIMESTAMP
    import time
    # Immediately update memory cache (write-through)
    _DATA_CACHE = data
    _CACHE_TIMESTAMP = time.time()

    success = False
    if JSONBIN_API_KEY and JSONBIN_BIN_ID:
        try:
            url = f"https://api.jsonbin.io/v3/b/{JSONBIN_BIN_ID}"
            headers = {
                "Content-Type": "application/json",
                "X-Master-Key": JSONBIN_API_KEY
            }
            resp = httpx.put(url, headers=headers, json=data, timeout=10)
            if resp.status_code in (200, 201):
                logger.info(f"[JSONBIN] Saved data to Jsonbin.io")
                success = True
            else:
                logger.error(f"[JSONBIN ERROR] Status {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.error(f"[JSONBIN ERROR] Failed saving data to Jsonbin.io: {e}")

    try:
        with open(SUBSCRIBERS_FILE, "w") as f:
            json.dump(data, f)
        if not (JSONBIN_API_KEY and JSONBIN_BIN_ID):
            success = True
    except Exception as e:
        logger.error(f"Error writing local subscribers file: {e}")

    return success

def get_subscribers() -> list:
    return get_jsonbin_data().get("subscribers", [])

def _save_subscribers(subs: list) -> bool:
    data = get_jsonbin_data()
    data["subscribers"] = subs
    return _save_jsonbin_data(data)

def add_subscriber(chat_id: int) -> bool:
    subs = get_subscribers()
    if chat_id not in subs:
        subs.append(chat_id)
        return _save_subscribers(subs)
    return False

def remove_subscriber(chat_id: int) -> bool:
    subs = get_subscribers()
    if chat_id in subs:
        subs.remove(chat_id)
        return _save_subscribers(subs)
    return False

def get_auto_subscribers() -> list:
    return get_jsonbin_data().get("auto_subscribers", [])

def _save_auto_subscribers(auto_subs: list) -> bool:
    data = get_jsonbin_data()
    data["auto_subscribers"] = auto_subs
    return _save_jsonbin_data(data)

def add_auto_subscriber(chat_id: int) -> bool:
    auto_subs = get_auto_subscribers()
    if chat_id not in auto_subs:
        auto_subs.append(chat_id)
        return _save_auto_subscribers(auto_subs)
    return False

def remove_auto_subscriber(chat_id: int) -> bool:
    auto_subs = get_auto_subscribers()
    if chat_id in auto_subs:
        auto_subs.remove(chat_id)
        return _save_auto_subscribers(auto_subs)
    return False

def is_auto_subscribed(chat_id: int) -> bool:
    return chat_id in get_auto_subscribers()

def get_allotment_subscriptions() -> list:
    return get_jsonbin_data().get("allotment_subscriptions", [])

def add_allotment_subscription(chat_id: int, ipo_name: str, registrar: str, pans: list, ignored_matches: list = None) -> bool:
    data = get_jsonbin_data()
    allot_subs = data.get("allotment_subscriptions", [])
    import hashlib
    sub_id = f"{chat_id}_{registrar}_{hashlib.md5(ipo_name.lower().encode()).hexdigest()[:8]}"
    
    ignored = list(set(ignored_matches or []))
    
    for item in allot_subs:
        if item.get("id") == sub_id or (item.get("chat_id") == chat_id and item.get("registrar") == registrar and item.get("ipo_name") == ipo_name):
            item["id"] = sub_id
            item["pans"] = pans
            item["status"] = "ACTIVE"
            if ignored:
                existing_ignored = item.get("ignored_matches", [])
                item["ignored_matches"] = list(set(existing_ignored + ignored))
            return _save_jsonbin_data(data)
            
    allot_subs.append({
        "id": sub_id,
        "chat_id": chat_id,
        "ipo_name": ipo_name,
        "registrar": registrar,
        "pans": pans,
        "ignored_matches": ignored,
        "notified_matches": [],
        "created_at": datetime.now().isoformat(),
        "status": "ACTIVE"
    })
    data["allotment_subscriptions"] = allot_subs
    return _save_jsonbin_data(data)

def ignore_subscription_matches(sub_id: str, matches_to_ignore: list = None) -> bool:
    data = get_jsonbin_data()
    allot_subs = data.get("allotment_subscriptions", [])
    updated = False
    for item in allot_subs:
        if item.get("id") == sub_id:
            existing_ignored = item.get("ignored_matches", [])
            to_add = matches_to_ignore if matches_to_ignore is not None else item.get("notified_matches", [])
            item["ignored_matches"] = list(set(existing_ignored + to_add))
            item["notified_matches"] = []
            updated = True
            break
    if updated:
        data["allotment_subscriptions"] = allot_subs
        return _save_jsonbin_data(data)
    return False

def update_subscription_notified_matches(sub_id: str, notified_matches: list) -> bool:
    data = get_jsonbin_data()
    allot_subs = data.get("allotment_subscriptions", [])
    updated = False
    for item in allot_subs:
        if item.get("id") == sub_id:
            item["notified_matches"] = notified_matches
            updated = True
            break
    if updated:
        data["allotment_subscriptions"] = allot_subs
        return _save_jsonbin_data(data)
    return False

def remove_allotment_subscription(sub_id: str) -> bool:
    data = get_jsonbin_data()
    allot_subs = data.get("allotment_subscriptions", [])
    initial_len = len(allot_subs)
    allot_subs = [item for item in allot_subs if item.get("id") != sub_id]
    if len(allot_subs) != initial_len:
        data["allotment_subscriptions"] = allot_subs
        return _save_jsonbin_data(data)
    return False



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


async def fetch_eligible_auto_ipos(session: Optional[httpx.AsyncClient] = None) -> list[dict]:
    """
    Fetch closed/active Mainboard IPOs with GMP >= 10.0% from Groww and InvestorGain.
    Returns list of dicts with keys: name, symbol, registrar, gmp_pct, gmp_raw
    """
    async def _fetch(client: httpx.AsyncClient) -> list[dict]:
        # 1. Fetch Mainboard unlisted closed IPOs from Groww
        try:
            g_resp = await client.get(_GROWW_CLOSED, headers=_HEADERS)
            g_resp.raise_for_status()
            g_data = g_resp.json().get("ipoList", [])
        except Exception as e:
            logger.error(f"[AUTO-SYNC] Error fetching closed IPOs from Groww: {e}")
            return []
        
        mainboard_closed = []
        import time
        import datetime as dt
        today = dt.datetime.now().date()
        now_ms = int(time.time() * 1000)
        for item in g_data:
            if item.get("isSme"):
                continue  # skip SME
            if item.get("listingPrice") is not None:
                continue  # already listed
            ts = item.get("listingTimestamp") or 0
            if ts and ts < now_ms:
                continue

            name = item.get("companyName", "").strip()
            if not name:
                continue
            symbol = item.get("symbol", "")
            rta_link = (item.get("rtaLink") or "").lower()
            registrar = "mufg"
            for key, val in _RTA_MAP.items():
                if key in rta_link:
                    registrar = val
                    break

            closing_str = item.get("closingDate")
            fallback_allot_date = None
            if closing_str:
                try:
                    fallback_allot_date = dt.datetime.strptime(closing_str, "%Y-%m-%d").date() + dt.timedelta(days=1)
                except Exception:
                    pass

            mainboard_closed.append({
                "name": name,
                "symbol": symbol,
                "registrar": registrar,
                "fallback_allot_date": fallback_allot_date
            })

        if not mainboard_closed:
            return []

        # 2. Fetch GMP percentage and Basis of Allotment date from InvestorGain
        now = datetime.now()
        month = now.month
        year = now.year
        fy = f"{year}-{str(year + 1)[2:]}" if month >= 4 else f"{year - 1}-{str(year)[2:]}"
        ig_url = f"https://webnodejs.investorgain.com/cloud/v2/report/data-read/331/1/{month}/{year}/{fy}/0/ipo?search=&v=21-18"
        try:
            ig_resp = await client.get(ig_url, headers=_HEADERS)
            ig_data = ig_resp.json().get("reportTableData", []) if ig_resp.status_code == 200 else []
        except Exception as e:
            logger.error(f"[AUTO-SYNC] Error fetching GMP data from InvestorGain: {e}")
            ig_data = []

        gmp_map = {}
        for item in ig_data:
            ig_name = item.get("~ipo_name", "").strip()
            if not ig_name:
                continue
            try:
                gmp_pct = float(item.get("~gmp_percent_calc", "0"))
            except ValueError:
                gmp_pct = 0.0

            boa_str = item.get("~Srt_BoA_Dt") or ""
            boa_date = None
            if boa_str:
                try:
                    boa_date = dt.datetime.strptime(boa_str, "%Y-%m-%d").date()
                except Exception:
                    pass

            gmp_map[ig_name.lower()] = {
                "name": ig_name,
                "gmp_pct": gmp_pct,
                "gmp_raw": item.get("GMP", ""),
                "boa_date": boa_date
            }

        # 3. Filter mainboard IPOs with GMP >= 10% AND allotment date is today or due (not in advance)
        eligible = []
        for mb in mainboard_closed:
            name_low = mb["name"].lower()
            matched_gmp = None
            for ig_k, ig_v in gmp_map.items():
                if ig_k in name_low or name_low in ig_k or any(w in ig_k for w in name_low.split() if len(w) > 3):
                    matched_gmp = ig_v
                    break

            allot_date = (matched_gmp and matched_gmp.get("boa_date")) or mb.get("fallback_allot_date")
            is_allotment_due = allot_date is not None and allot_date <= today

            if is_allotment_due and matched_gmp and matched_gmp["gmp_pct"] >= 10.0:
                eligible.append({
                    "name": mb["name"],
                    "symbol": mb["symbol"],
                    "registrar": mb["registrar"],
                    "gmp_pct": matched_gmp["gmp_pct"],
                    "gmp_raw": matched_gmp["gmp_raw"],
                    "allotment_date": allot_date
                })

        return eligible

    if session is not None:
        return await _fetch(session)
    else:
        async with httpx.AsyncClient(timeout=15, headers=_HEADERS) as client:
            return await _fetch(client)


async def sync_auto_subscriptions() -> int:
    """
    Sync all active auto-subscribers with current eligible Mainboard IPOs (GMP >= 10%).
    Performs batched deduplication and writes once to avoid JSONBin rate limits.
    Runs completely silently in the background.
    Returns the number of new subscriptions added.
    """
    data = get_jsonbin_data()
    auto_subs = data.get("auto_subscribers", [])
    if not auto_subs:
        return 0

    pans = get_pan_list()
    if not pans:
        logger.warning("[AUTO-SYNC] No PANs configured in PAN_LIST.")
        return 0

    try:
        eligible_ipos = await fetch_eligible_auto_ipos()
    except Exception as e:
        logger.error(f"[AUTO-SYNC ERROR] Failed fetching eligible IPOs: {e}")
        return 0

    if not eligible_ipos:
        logger.info("[AUTO-SYNC] No eligible Mainboard IPOs with GMP >= 10% found.")
        return 0

    allot_subs = data.get("allotment_subscriptions", [])
    import hashlib

    # Build existing lookup set of (chat_id, registrar, ipo_name) and sub_id
    existing_keys = set()
    for item in allot_subs:
        existing_keys.add(item.get("id"))
        c_id = item.get("chat_id")
        reg = (item.get("registrar") or "").lower()
        ipo_n = (item.get("ipo_name") or "").strip().lower()
        if c_id and reg and ipo_n:
            existing_keys.add((c_id, reg, ipo_n))

    added_count = 0
    for chat_id in auto_subs:
        for ipo in eligible_ipos:
            ipo_name = ipo["name"]
            registrar = ipo["registrar"]
            sub_id = f"{chat_id}_{registrar}_{hashlib.md5(ipo_name.lower().encode()).hexdigest()[:8]}"
            key_tuple = (chat_id, registrar.lower(), ipo_name.strip().lower())

            if sub_id in existing_keys or key_tuple in existing_keys:
                continue  # already subscribed! Skip to prevent duplicates

            allot_subs.append({
                "id": sub_id,
                "chat_id": chat_id,
                "ipo_name": ipo_name,
                "registrar": registrar,
                "pans": pans,
                "ignored_matches": [],
                "notified_matches": [],
                "created_at": datetime.now().isoformat(),
                "status": "ACTIVE"
            })
            existing_keys.add(sub_id)
            existing_keys.add(key_tuple)
            added_count += 1
            logger.info(f"[AUTO-SYNC] Subscribed chat_id {chat_id} to '{ipo_name}' ({registrar.upper()}, GMP: {ipo['gmp_pct']}%)")

    if added_count > 0:
        data["allotment_subscriptions"] = allot_subs
        _save_jsonbin_data(data)

    return added_count


# END NEW IMPLEMENTATION -----------------------------------------------


async def fetch_ipo_market_data() -> List[dict]:
    """Fetch IPO market data including GMP from InvestorGain API."""
    import html
    # Get current month and year
    now = datetime.now()
    month = now.month
    year = now.year

    # Determine financial year (April to March)
    if month >= 4:
        fy = f"{year}-{str(year + 1)[2:]}"  # e.g., "2025-26"
    else:
        fy = f"{year - 1}-{str(year)[2:]}"  # e.g., "2024-25"

    # Build dynamic URL
    url = f"https://webnodejs.investorgain.com/cloud/v2/report/data-read/331/1/{month}/{year}/{fy}/0/ipo?search=&v=21-18"
    logger.info(f"Fetching market data from: {url}")

    async with httpx.AsyncClient(timeout=15, headers=_HEADERS) as session:
        try:
            resp = await session.get(url)
            print("Response:", resp.text)
            resp.raise_for_status()
            data = resp.json()

            # Extract reportTableData
            gmp_list = data.get("reportTableData", [])

            # Process and format the data
            processed_data = []
            for item in gmp_list:
                # Extract IPO name
                name = item.get("~ipo_name", "").strip()
                if not name:
                    name_field = item.get("Name", "")
                    if "title=\"" in name_field:
                        name = name_field.split('title="')[1].split('"')[0].strip()
                    else:
                        name = name_field.strip()

                if not name:
                    name = item.get("company_short_name", "").strip()

                if not name:
                    continue

                # Skip items that are already closed (past listing date)
                import datetime as dt
                try:
                    listing_date = dt.datetime.strptime(item.get("~Str_Listing", ""), "%Y-%m-%d")
                    if listing_date.date() < dt.datetime.now().date():
                        continue
                except:
                    # If date parsing fails, include the item
                    pass

                # Extract GMP value from HTML
                gmp_field = item.get("GMP", "")
                gmp = "0"
                gmp_percent = "0%"
                if "&#8377;" in gmp_field:
                    # Extract rupee value
                    if "<b>" in gmp_field:
                        gmp_raw = gmp_field.split("<b>")[1].split("</b>")[0]
                        if gmp_raw != "--":
                            gmp = gmp_raw
                            # Extract percentage if present
                            if "(" in gmp_field and "%" in gmp_field:
                                gmp_percent = gmp_field.split("(")[1].split(")")[0]

                def clean_val(val):
                    if not val:
                        return "NA"
                    return val.split("<")[0].strip()

                listing = item.get("Listing", "-")

                processed_data.append({
                    "name": html.unescape(name).strip(),
                    "gmp": f"{gmp} ({gmp_percent})",
                    "open": clean_val(item.get("Open", "NA")),
                    "close": clean_val(item.get("Close", "NA")),
                    "boa_date": clean_val(item.get("BoA Dt", "NA")),
                    "listing": clean_val(listing),
                })

            logger.info(f"Processed {len(processed_data)} IPOs")
            return processed_data
        except Exception as e:
            logger.error(f"Error fetching IPO market data: {e}")
            logger.error(f"URL was: {url}")
            return []


async def fetch_ipo_allotment_dates() -> dict:
    """Fetch mapping of company names / symbols to their estimated allotment dates."""
    import datetime as dt
    dates_map: dict = {}
    
    # 1. Fetch Groww closed IPOs
    try:
        async with httpx.AsyncClient(timeout=15, headers=_HEADERS) as session:
            resp = await session.get(_GROWW_CLOSED)
            if resp.status_code == 200:
                data = resp.json()
                for item in data.get("ipoList", []):
                    closing_str = item.get("closingDate")
                    name = (item.get("companyName") or "").strip().lower()
                    symbol = (item.get("symbol") or "").strip().lower()
                    if closing_str:
                        try:
                            c_date = dt.datetime.strptime(closing_str, "%Y-%m-%d").date()
                            # Allotment is typically T+1 day after closing
                            allotment_date = c_date + dt.timedelta(days=1)
                            if name:
                                dates_map[name] = allotment_date
                            if symbol:
                                dates_map[symbol] = allotment_date
                        except Exception:
                            pass
    except Exception as e:
        logger.error(f"Error fetching Groww IPO dates: {e}")
        
    # 2. Fetch InvestorGain data
    try:
        market_data = await fetch_ipo_market_data()
        for item in market_data:
            name = (item.get("name") or "").strip().lower()
            boa = (item.get("boa_date") or "").strip()
            if name and boa and boa != "NA":
                parsed_date = None
                for fmt in ["%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d", "%d-%b"]:
                    try:
                        d = dt.datetime.strptime(boa, fmt).date()
                        if fmt == "%d-%b":
                            d = d.replace(year=dt.datetime.now().year)
                        parsed_date = d
                        break
                    except Exception:
                        continue
                if parsed_date:
                    dates_map[name] = parsed_date
    except Exception as e:
        logger.error(f"Error fetching InvestorGain IPO dates: {e}")
        
    return dates_map


def is_subscription_expired(sub: dict, ipo_dates: dict) -> tuple[bool, str]:
    """
    Check if an allotment subscription is expired (allotment date passed 2+ days ago).
    Returns (is_expired, reason).
    """
    import datetime as dt
    from fuzzy_matcher import FuzzyMatcher
    
    ipo_name = sub.get("ipo_name", "").strip().lower()
    created_at_str = sub.get("created_at")
    today = dt.datetime.now().date()
    
    # 1. Direct or Fuzzy match in ipo_dates
    matched_date = ipo_dates.get(ipo_name)
    if not matched_date and ipo_dates:
        matcher = FuzzyMatcher(confidence_threshold=0.7)
        for key, d in ipo_dates.items():
            if matcher.calculate_similarity(ipo_name, key) >= 0.7:
                matched_date = d
                break
                
    if matched_date:
        cutoff = matched_date + dt.timedelta(days=2)
        if today > cutoff:
            return True, f"allotment date ({matched_date.strftime('%d %b %Y')}) passed 2+ days ago"
            
    # 2. Fallback: If created_at is older than 5 days
    if created_at_str:
        try:
            created_dt = dt.datetime.fromisoformat(created_at_str)
            if (dt.datetime.now() - created_dt).days >= 5:
                return True, f"subscription created 5+ days ago ({created_dt.strftime('%d %b %Y')}) with no status release"
        except Exception:
            pass
            
    return False, ""



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





def build_auto_sub_button(chat_id: int) -> InlineKeyboardButton:
    """Build dynamic single toggle button based on current user auto-subscription state."""
    is_sub = is_auto_subscribed(chat_id)
    if is_sub:
        return InlineKeyboardButton("🔕 Disable Auto-Tracking (GMP ≥ 10%)", callback_data="toggle_auto:off")
    else:
        return InlineKeyboardButton("🔔 Enable Auto-Tracking (GMP ≥ 10%)", callback_data="toggle_auto:on")


def build_keyboard(catalogue: List[dict], chat_id: Optional[int] = None) -> InlineKeyboardMarkup:
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

    if chat_id is not None:
        buttons.append([build_auto_sub_button(chat_id)])

    return InlineKeyboardMarkup(buttons)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):  # /start or /list
    chat_id = update.effective_chat.id
    catalogue = await fetch_ipo_catalogue()
    keyboard = build_keyboard(catalogue, chat_id=chat_id)
    await update.message.reply_text("Select an IPO to fetch allotment status or toggle auto-tracking:", reply_markup=keyboard)


def get_pan_list() -> List[str]:
    pans_env = os.getenv("PAN_LIST", "").strip()
    if not pans_env:
        return []
    return [p.strip().upper() for p in pans_env.split(",") if p.strip()]


def format_allotment_result(pan: str, result: Union[str, Exception]) -> str:
    """Format individual PAN allotment status with clean indicators and status details."""
    if isinstance(result, Exception):
        return f"⚠️ {pan} - Status: Error fetching status"
    
    text = str(result).strip()
    if not text:
        return f"🔴 {pan} - Status: Not Allotted"
    
    if any(err_kw in text.lower() for err_kw in ["error fetching", "unable to fetch"]):
        return f"⚠️ {pan} - Status: Error fetching status"
    
    if any(nf_kw in text.lower() for nf_kw in ["no record", "not available", "not yet available"]):
        return f"🔴 {pan} - Status: Not Allotted"

    def _format_single(record_text: str) -> str:
        name = None
        name_match = re.search(r"Name:\s*([^|\n\r]+)", record_text, re.IGNORECASE)
        if name_match:
            extracted = name_match.group(1).strip()
            if extracted and extracted.upper() not in ["NONE", "NULL", ""]:
                name = extracted

        shares_str = None
        allot_match = re.search(r"(?:ALLOTED|Allotted|ALLOT|Shares):\s*([0-9]+(?:\.[0-9]+)?)", record_text, re.IGNORECASE)
        if allot_match:
            shares_str = allot_match.group(1).strip()

        display_name = name if name else pan

        is_allotted = False
        shares_count = 0
        if shares_str:
            try:
                val = float(shares_str)
                if val > 0:
                    is_allotted = True
                    shares_count = int(val) if val.is_integer() else val
            except ValueError:
                pass

        if is_allotted:
            return f"🟢 {display_name} - Status: Allotted | Shares: {shares_count}"
        else:
            return f"🔴 {display_name} - Status: Not Allotted"

    if "\n\n" in text:
        sub_records = [r for r in text.split("\n\n") if r.strip()]
        return "\n".join(_format_single(sub) for sub in sub_records)
    
    return _format_single(text)


async def handle_ipo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Handle toggle auto-subscription button
    if query.data.startswith("toggle_auto:"):
        action = query.data.split(":", 1)[1]  # "on" or "off"
        chat_id = update.effective_chat.id
        pans = get_pan_list()

        if action == "on":
            if not pans:
                await query.edit_message_text("❌ No PANs configured. Please set `PAN_LIST` in environment variables.", parse_mode="Markdown")
                return
            add_auto_subscriber(chat_id)
            await sync_auto_subscriptions()
            eligible = await fetch_eligible_auto_ipos()

            lines = [
                "✅ *Auto-Subscription Enabled!*\n",
                "You are now automatically subscribed to all **Mainboard IPOs** with **GMP ≥ 10%** on their allotment day.\n"
            ]
            if eligible:
                lines.append("*Currently Subscribed for Today:*")
                for ipo in eligible:
                    lines.append(f"• *{ipo['name']}* ({ipo['registrar'].upper()}) – GMP: {ipo['gmp_pct']:.1f}%")
            else:
                lines.append("No Mainboard IPOs currently have allotment scheduled for today.")

            new_keyboard = InlineKeyboardMarkup([
                [build_auto_sub_button(chat_id)]
            ])
            try:
                await query.edit_message_text("\n".join(lines), reply_markup=new_keyboard, parse_mode="Markdown")
            except Exception:
                await query.edit_message_text("\n".join(lines), reply_markup=new_keyboard)
        else:
            remove_auto_subscriber(chat_id)
            new_keyboard = InlineKeyboardMarkup([
                [build_auto_sub_button(chat_id)]
            ])
            try:
                await query.edit_message_text(
                    "🚫 *Auto-Subscription Disabled!*\n\n"
                    "You will no longer be automatically subscribed to future Mainboard IPOs.\n\n"
                    "*(Note: Existing pending subscriptions remain active until allotment is announced.)*",
                    reply_markup=new_keyboard,
                    parse_mode="Markdown"
                )
            except Exception:
                await query.edit_message_text(
                    "🚫 *Auto-Subscription Disabled!*\n\n"
                    "You will no longer be automatically subscribed to future Mainboard IPOs.",
                    reply_markup=new_keyboard
                )
        return
    
    # Handle fuzzy match confirmations
    if query.data.startswith("fuzz_"):
        from confirmation_handler import confirmation_handler
        handled = await confirmation_handler.handle_confirmation_response(update, context)
        if handled:
            return

    # Handle ignoring fuzzy match candidates from polling notification
    if query.data.startswith("ignore_fuzz:"):
        parts = query.data.split(":", 1)
        sub_id = parts[1]
        ignore_subscription_matches(sub_id)
        await query.edit_message_text(
            "🔔 *Subscription Updated*\n\n"
            "Got it! We won't notify you about these potential matches again. We will continue monitoring for allotment status releases.",
            parse_mode="Markdown"
        )
        return

    # Handle subscription for allotment alerts
    if query.data.startswith("sub_allot:"):
        parts = query.data.split(":", 2)
        _, registrar, ipo_name = parts
        chat_id = update.effective_chat.id
        pans = get_pan_list()
        if add_allotment_subscription(chat_id, ipo_name, registrar, pans):
            await query.edit_message_text(
                f"🔔 *Subscription Confirmed!*\n\n"
                f"IPO: *{ipo_name}*\n"
                f"Registrar: *{registrar.upper()}*\n\n"
                f"We will check periodically and send you an alert as soon as the allotment status is released.",
                parse_mode="Markdown"
            )
        else:
            await query.edit_message_text("❌ Failed to register subscription. Please try again.")
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
            
            is_not_found = any(k in test_result.lower() for k in ["not available", "not yet available", "not found", "unable to fetch"])
            if is_not_found:
                if hasattr(client, 'find_fuzzy_matches'):
                    fuzzy_matches = await client.find_fuzzy_matches(session, ipo_name)
                    if fuzzy_matches:
                        from confirmation_handler import confirmation_handler
                        context.user_data['fuzzy_pans'] = pans
                        context.user_data['fuzzy_registrar'] = registrar
                        await confirmation_handler.request_confirmation(
                            update, context, ipo_name, fuzzy_matches, registrar, pans[0]
                        )
                        return
                
                sub_keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔔 Subscribe for Allotment Alerts", callback_data=f"sub_allot:{registrar}:{ipo_name.replace(':',' ')}")]
                ])
                await query.edit_message_text(
                    f"❌ Allotment status for *'{ipo_name}'* is not available on {registrar.upper()} yet.\n\n"
                    f"Click below to get notified as soon as status is released:",
                    reply_markup=sub_keyboard,
                    parse_mode="Markdown"
                )
                return
        except Exception as e:
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
                    pass
        
        # Normal processing for all PANs (either company found or fuzzy not available/not needed)
        tasks = [client.status_by_pan(session, pan=pan, company_name=ipo_name) for pan in pans]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    # Format and send results for all PANs
    lines = [f"*IPO:* {ipo_name}  *(Registrar: {registrar.upper()})*\n"]
    for pan, result in zip(pans, results):
        if isinstance(result, Exception):
            logger.error("[ERROR] Exception fetching status for PAN %s: %s", pan, result)
        lines.append(format_allotment_result(pan, result))

    full_text = "\n".join(lines)
    logger.info("[ACTION] Completed allotment check for '%s' | PAN count: %d | Total response length: %d chars", ipo_name, len(pans), len(full_text))

    # Telegram message length limit is 4096. Chunk into segments of <= 4000 chars.
    MAX_LEN = 3800
    chunks = []
    current_chunk = ""
    for line in lines:
        if len(current_chunk) + len(line) + 1 > MAX_LEN:
            if current_chunk:
                chunks.append(current_chunk)
            current_chunk = line
        else:
            current_chunk = f"{current_chunk}\n{line}" if current_chunk else line
    if current_chunk:
        chunks.append(current_chunk)

    if chunks:
        # Edit initial callback message with first chunk
        try:
            await query.edit_message_text(text=chunks[0], parse_mode="Markdown")
        except Exception:
            await query.edit_message_text(text=chunks[0])

        # Send remaining chunks as new messages
        for chunk in chunks[1:]:
            try:
                await context.bot.send_message(chat_id=query.message.chat_id, text=chunk, parse_mode="Markdown")
            except Exception:
                await context.bot.send_message(chat_id=query.message.chat_id, text=chunk)


async def _resolve_registrar(session: httpx.AsyncClient, scrip_cd: str, ipo_no: str, start_dt: str) -> str:
    """Fetch DisplayIPO HTML and extract registrar short code."""
    url = (
        "https://www.bseindia.com/markets/publicIssues/DisplayIPO.aspx"
        f"?id={scrip_cd}&type=IPO&idtype=1&status=L&IPONo={ipo_no}&startdt={start_dt}"
    )
    logger.info("[REQUEST] GET %s (Resolving registrar from BSE)", url)
    reg_full = ""
    try:
        page = await session.get(url, headers=_HEADERS, timeout=10)
        page.raise_for_status()
        logger.info("[RESPONSE] GET BSE page | Status: %s", page.status_code)
        soup = BeautifulSoup(page.text, "html.parser")
        label = soup.find(string=re.compile(r"Registrar", re.I))
        if label:
            link = label.find_next("a")
            if link:
                reg_full = link.get_text(strip=True).upper()
        logger.info("[EXTRACT] BSE Registrar string: '%s'", reg_full)
    except Exception as exc:
        logger.error("[ERROR] Failed resolving registrar from BSE: %s", exc)

    if "INTIME" in reg_full or "MUFG" in reg_full or "LINK" in reg_full:
        return "mufg"
    if "KFIN" in reg_full:
        return "kfin"
    if "MAS" in reg_full:
        return "mas"
    if "BIGSHARE" in reg_full:
        return "bigshare"
    return "mufg"


async def subscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Subscribe user to daily updates."""
    chat_id = update.effective_chat.id
    added = add_subscriber(chat_id)
    if added:
        await update.message.reply_text(
            "🔔 *Subscribed!*\n\nYou will now receive daily IPO market updates every morning at 9:00 AM.",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "ℹ️ You are already subscribed to daily updates.",
            parse_mode="Markdown"
        )


async def unsubscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Unsubscribe user from daily updates."""
    chat_id = update.effective_chat.id
    removed = remove_subscriber(chat_id)
    if removed:
        await update.message.reply_text(
            "🔕 *Unsubscribed!*\n\nYou will no longer receive daily IPO market updates.",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "ℹ️ You were not subscribed to daily updates.",
            parse_mode="Markdown"
        )


async def send_daily_updates(application: Application) -> None:
    """Broadcast daily IPO updates to all subscribers."""
    logger.info("Triggering daily IPO updates for subscribers...")
    market_data = await fetch_ipo_market_data()
    if not market_data:
        logger.warning("No market data fetched for daily updates.")
        return

    # Format the message
    response_lines = ["📊 *Daily IPO Market Data Update*\n"]
    for ipo in market_data:
        lines = [
            f"\n*{ipo['name']}*",
            f"📈 GMP: ₹{ipo['gmp']}",
            f"📅 Open: {ipo['open']}",
            f"📅 Close: {ipo['close']}",
            f"📋 Allotment: {ipo['boa_date']}",
            f"🔔 Listing: {ipo['listing']}",
            "────────────────────"
        ]
        response_lines.extend(lines)

    message_text = "\n".join(response_lines)
    
    subs = get_subscribers()
    logger.info(f"Sending daily update to {len(subs)} subscribers.")
    for chat_id in subs:
        try:
            await application.bot.send_message(
                chat_id=chat_id,
                text=message_text,
                parse_mode="Markdown"
            )
            await asyncio.sleep(0.05)  # Avoid hitting rate limits
        except Exception as e:
            logger.error(f"Failed to send daily update to {chat_id}: {e}")


async def subscribe_all_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Enable auto-subscription to all Mainboard IPOs with GMP >= 10%."""
    chat_id = update.effective_chat.id
    pans = get_pan_list()
    if not pans:
        await update.message.reply_text("❌ No PANs configured. Please set `PAN_LIST` in environment variables.", parse_mode="Markdown")
        return

    add_auto_subscriber(chat_id)

    # Immediately sync current eligible IPOs
    await sync_auto_subscriptions()
    eligible = await fetch_eligible_auto_ipos()

    lines = [
        "✅ *Auto-Subscription Enabled!*\n",
        "You are now automatically subscribed to all **Mainboard IPOs** with **GMP ≥ 10%** on their allotment day.\n"
    ]

    if eligible:
        lines.append("*Currently Subscribed for Today:*")
        for ipo in eligible:
            lines.append(f"• *{ipo['name']}* ({ipo['registrar'].upper()}) – GMP: {ipo['gmp_pct']:.1f}%")
    else:
        lines.append("No Mainboard IPOs currently have allotment scheduled for today. You will be automatically enrolled on the day eligible IPO allotments open.")

    lines.append("\nUse /unsubscribe_all anytime to disable auto-tracking.")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def unsubscribe_all_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Disable auto-subscription to Mainboard IPOs."""
    chat_id = update.effective_chat.id
    removed = remove_auto_subscriber(chat_id)
    if removed:
        await update.message.reply_text(
            "🚫 *Auto-Subscription Disabled!*\n\n"
            "You will no longer be automatically subscribed to future Mainboard IPOs.\n\n"
            "*(Note: Existing pending subscriptions remain active until allotment is announced.)*",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "ℹ️ You are not currently auto-subscribed.\n\nUse /subscribe_all to enable automatic tracking for Mainboard IPOs with GMP ≥ 10%.",
            parse_mode="Markdown"
        )


async def auto_subscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current auto-subscription status with an inline toggle button."""
    chat_id = update.effective_chat.id
    is_sub = is_auto_subscribed(chat_id)

    keyboard = InlineKeyboardMarkup([
        [build_auto_sub_button(chat_id)]
    ])

    if is_sub:
        eligible = await fetch_eligible_auto_ipos()
        lines = [
            "⚡ *Auto-Tracking Status: ACTIVE (Enabled)*\n",
            "You are automatically subscribed to all **Mainboard IPOs** with **GMP ≥ 10%** on their allotment day.\n"
        ]
        if eligible:
            lines.append("*Subscribed for Allotment Today:*")
            for ipo in eligible:
                lines.append(f"• *{ipo['name']}* ({ipo['registrar'].upper()}) – GMP: {ipo['gmp_pct']:.1f}%")
        else:
            lines.append("No Mainboard IPOs currently have allotment scheduled for today.")
    else:
        lines = [
            "⚡ *Auto-Tracking Status: INACTIVE (Disabled)*\n",
            "Click the button below to automatically track all **Mainboard IPOs** with **GMP ≥ 10%** on their allotment day without manual selection."
        ]

    await update.message.reply_text("\n".join(lines), reply_markup=keyboard, parse_mode="Markdown")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help message with available commands."""
    help_text = """
📋 *Available Commands:*

*IPO Services:*
/closed_ipo - Show closed IPOs and check allotment status
/all_active_ipo - Show all active IPOs with GMP data
/auto_subscribe - Toggle auto-tracking for Mainboard IPOs (GMP ≥ 10%)
/subscribe_all - Auto-subscribe to all Mainboard IPOs with GMP ≥ 10%
/unsubscribe_all - Disable automatic tracking for future IPOs
/subscribe - Subscribe to daily updates at 9:00 AM IST
/unsubscribe - Unsubscribe from daily updates

*Other:*
/health - Check if bot is running
/help - Show this help message

*How to use:*
1. Use /auto_subscribe to toggle auto-tracking with 1 click
2. Use /closed_ipo to see closed IPOs and check allotment status
3. Click on an IPO name to check your allotment status
4. Use /all_active_ipo to see active IPOs with GMP data
5. Use /subscribe to receive automated updates every morning
    """
    await update.message.reply_text(help_text, parse_mode="Markdown")


async def setup_bot_commands(application: Application) -> None:
    """Set up bot commands for the menu button."""
    commands = [
        BotCommand("help", "Show help message and instructions"),
        BotCommand("health", "Check if bot is running"),
        BotCommand("closed_ipo", "Show closed IPOs and check allotment status"),
        BotCommand("all_active_ipo", "Show all active IPOs with GMP data"),
        BotCommand("auto_subscribe", "Toggle auto-tracking (GMP ≥ 10%)"),
        BotCommand("subscribe_all", "Enable auto-tracking for Mainboard IPOs"),
        BotCommand("unsubscribe_all", "Disable auto-tracking for Mainboard IPOs"),
        BotCommand("subscribe", "Subscribe to daily morning updates"),
        BotCommand("unsubscribe", "Unsubscribe from daily updates"),
    ]
    await application.bot.set_my_commands(commands)
    logger.info("Bot commands have been set up")


async def post_init(application: Application) -> None:
    """Initialize bot commands after startup and start Tornado HTTP server."""
    await setup_bot_commands(application)

    PORT = int(os.environ.get("PORT", 8000))
    RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL")

    # Access Settings to get BOT_TOKEN
    settings = Settings()

    class CronHandler(tornado.web.RequestHandler):
        async def get(self):
            # Run broadcast in background so HTTP response is returned immediately
            asyncio.create_task(send_daily_updates(application))
            self.write("Daily updates triggered successfully.")

    class HealthHandler(tornado.web.RequestHandler):
        def get(self):
            self.write("OK")

    handlers = [
        (r"/health", HealthHandler),
        (r"/cron-daily-update", CronHandler),
    ]

    # In Webhook mode, Tornado also handles incoming Telegram webhook updates
    if RENDER_EXTERNAL_URL:
        class TelegramWebhookHandler(tornado.web.RequestHandler):
            async def post(self):
                try:
                    data = json.loads(self.request.body)
                    update = Update.de_json(data, application.bot)
                    await application.process_update(update)
                    self.write("OK")
                except Exception as e:
                    logger.error(f"Error handling Telegram webhook update: {e}")
                    self.set_status(500)
                    self.write("Error")

        handlers.append((f"/{settings.BOT_TOKEN}", TelegramWebhookHandler))
        logger.info(f"Registered webhook endpoint: /{settings.BOT_TOKEN}")

    app = tornado.web.Application(handlers)
    app.listen(PORT, address="0.0.0.0")
    logger.info(f"Tornado HTTP server listening on 0.0.0.0:{PORT}")


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=logging.INFO
    )
    settings = Settings()

    application = Application.builder().token(settings.BOT_TOKEN).post_init(post_init).build()

    application.add_handler(CommandHandler("menu", help_command))
    application.add_handler(CommandHandler("start", start))  # legacy alias
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("closed_ipo", start))  # legacy alias
    application.add_handler(CommandHandler("all_active_ipo", market_command))  # new command for active IPOs
    application.add_handler(CommandHandler("subscribe", subscribe_command))
    application.add_handler(CommandHandler("unsubscribe", unsubscribe_command))
    application.add_handler(CommandHandler("auto_subscribe", auto_subscribe_command))
    application.add_handler(CommandHandler("auto_tracking", auto_subscribe_command))
    application.add_handler(CommandHandler("subscribe_all", subscribe_all_command))
    application.add_handler(CommandHandler("unsubscribe_all", unsubscribe_all_command))
    application.add_handler(CallbackQueryHandler(handle_ipo_callback, pattern=r"^(ipo:|fuzz_|sub_allot:|ignore_fuzz:|toggle_auto:)"))

    # Add a simple health check handler
    async def health_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("✅ Bot is running!")

    application.add_handler(CommandHandler("health", health_check))

    # Check if running on Render (webhook mode) or locally (polling mode)
    PORT = int(os.environ.get("PORT", 0))
    RENDER_EXTERNAL_HOSTNAME = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
    RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL") or (f"https://{RENDER_EXTERNAL_HOSTNAME}" if RENDER_EXTERNAL_HOSTNAME else None)

    if RENDER_EXTERNAL_URL and PORT:
        webhook_url = f"{RENDER_EXTERNAL_URL}/{settings.BOT_TOKEN}"
        logger.info(f"Starting in Webhook mode. Webhook URL: {webhook_url}")

        async def run_webhook_mode():
            await application.initialize()
            await post_init(application)
            await application.start()
            try:
                await application.bot.set_webhook(url=webhook_url)
                logger.info(f"Telegram webhook successfully set to: {webhook_url}")
            except Exception as e:
                logger.warning(f"Could not set Telegram webhook: {e}")
            while True:
                await asyncio.sleep(3600)

        try:
            asyncio.run(run_webhook_mode())
        except (KeyboardInterrupt, SystemExit):
            logger.info("Application stopped.")
    elif PORT:
        logger.info(f"Starting in Polling + Web Server mode on port {PORT}")
        async def run_polling_with_web():
            await application.initialize()
            await post_init(application)
            await application.start()
            await application.updater.start_polling(drop_pending_updates=True)
            while True:
                await asyncio.sleep(3600)

        try:
            asyncio.run(run_polling_with_web())
        except (KeyboardInterrupt, SystemExit):
            logger.info("Application stopped.")
    else:
        logger.info("Starting polling mode")
        application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
