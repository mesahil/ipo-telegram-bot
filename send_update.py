import asyncio
import logging
import os
import sys

import httpx

from bot import fetch_ipo_market_data, get_subscribers, sync_auto_subscriptions

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


async def run_standalone_update():
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        logger.error("BOT_TOKEN environment variable is missing.")
        sys.exit(1)

    # Sync any new Mainboard IPOs with GMP >= 10% for auto-subscribers
    try:
        await sync_auto_subscriptions()
    except Exception as e:
        logger.error(f"Error syncing auto-subscriptions in morning update: {e}")

    subs = get_subscribers()
    logger.info(f"Loaded {len(subs)} subscriber(s) for daily update.")
    if not subs:
        logger.info("No subscribers found. Exiting.")
        return

    market_data = await fetch_ipo_market_data()
    if not market_data:
        logger.warning("No market data fetched for daily updates.")
        return

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

    async with httpx.AsyncClient(timeout=20) as client:
        for chat_id in subs:
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            payload = {
                "chat_id": chat_id,
                "text": message_text,
                "parse_mode": "Markdown"
            }
            try:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    logger.info(f"Successfully sent daily update to chat_id: {chat_id}")
                else:
                    logger.error(f"Failed sending update to chat_id {chat_id}: {resp.status_code} - {resp.text}")
            except Exception as e:
                logger.error(f"Exception sending update to {chat_id}: {e}")
            await asyncio.sleep(0.05)


if __name__ == "__main__":
    asyncio.run(run_standalone_update())
