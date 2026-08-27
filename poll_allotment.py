import asyncio
import logging
import os
import sys

import httpx

from bot import (
    get_allotment_subscriptions,
    remove_allotment_subscription,
    update_subscription_notified_matches,
    fetch_ipo_allotment_dates,
    is_subscription_expired,
    get_client_for_registrar,
    format_allotment_result,
    sync_auto_subscriptions,
    RegistrarClient
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("poll_allotment")


async def process_subscriptions():
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        logger.error("BOT_TOKEN environment variable is missing.")
        sys.exit(1)

    # 1. Sync auto-subscriptions for mainboard IPOs with GMP >= 10%
    try:
        newly_synced = await sync_auto_subscriptions()
        if newly_synced:
            logger.info(f"[POLL] Auto-synced {newly_synced} new allotment subscription(s).")
    except Exception as e:
        logger.error(f"[POLL ERROR] Auto-sync failed: {e}")

    subs = get_allotment_subscriptions()
    logger.info(f"[POLL] Found {len(subs)} active allotment subscription(s).")
    if not subs:
        logger.info("[POLL] No pending subscriptions. Exiting cleanly.")
        return

    # Fetch latest IPO allotment dates for auto-expiration checks
    try:
        ipo_dates = await fetch_ipo_allotment_dates()
    except Exception as e:
        logger.error(f"[POLL ERROR] Failed to fetch IPO allotment dates: {e}")
        ipo_dates = {}

    async with httpx.AsyncClient(timeout=25) as session:
        for sub in subs:
            sub_id = sub.get("id")
            chat_id = sub.get("chat_id")
            ipo_name = sub.get("ipo_name")
            registrar = sub.get("registrar")
            pans = sub.get("pans", [])

            if not (chat_id and ipo_name and registrar and pans):
                logger.warning(f"[POLL SKIP] Invalid subscription data: {sub}")
                continue

            logger.info(f"[POLL CHECK] Checking '{ipo_name}' on {registrar.upper()} for chat_id={chat_id}")
            client: RegistrarClient = get_client_for_registrar(registrar)
            if not client:
                logger.error(f"[POLL ERROR] Registrar client '{registrar}' not found.")
                continue

            try:
                # Test first PAN to see if allotment status is available yet
                test_result = await client.status_by_pan(session, pan=pans[0], company_name=ipo_name)
                is_not_found = any(
                    k in test_result.lower()
                    for k in ["not available", "not yet available", "not found", "unable to fetch"]
                )

                if is_not_found:
                    # Check if subscription has expired (allotment date passed 2+ days ago)
                    expired, reason = is_subscription_expired(sub, ipo_dates)
                    if expired:
                        logger.info(f"[POLL EXPIRED] Removing subscription '{sub_id}' for '{ipo_name}': {reason}")
                        remove_allotment_subscription(sub_id)
                        tg_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
                        msg_text = (
                            f"ℹ️ *Subscription Auto-Expired*\n\n"
                            f"Your subscription for *'{ipo_name}'* on {registrar.upper()} was automatically removed.\n"
                            f"*Reason:* {reason}."
                        )
                        payload = {
                            "chat_id": chat_id,
                            "text": msg_text,
                            "parse_mode": "Markdown"
                        }
                        try:
                            await session.post(tg_url, json=payload)
                        except Exception as te:
                            logger.error(f"[POLL EXPIRED TG ERROR] Failed sending expiration alert: {te}")
                        continue

                    logger.info(f"[POLL PENDING] Status for '{ipo_name}' still not available. Checking fuzzy matches...")
                    if hasattr(client, 'find_fuzzy_matches'):
                        try:
                            fuzzy_matches = await client.find_fuzzy_matches(session, ipo_name)
                            if fuzzy_matches:
                                ignored_matches = set(sub.get("ignored_matches", []))
                                notified_matches = set(sub.get("notified_matches", []))

                                unignored_matches = [m for m in fuzzy_matches if m.match not in ignored_matches]
                                if unignored_matches:
                                    current_match_names = set(m.match for m in unignored_matches[:3])
                                    if current_match_names == notified_matches:
                                        logger.info(f"[POLL FUZZY] User already notified for fuzzy matches of '{ipo_name}'. Skipping duplicate notification.")
                                        continue

                                    logger.info(f"[POLL FUZZY] Found {len(unignored_matches)} new potential fuzzy match(es) for '{ipo_name}'.")
                                    keyboard_inline = []
                                    for m in unignored_matches[:3]:
                                        conf_str = f" ({int(m.confidence*100)}%)" if m.confidence > 0 else ""
                                        keyboard_inline.append([{
                                            "text": f"✅ {m.match}{conf_str}",
                                            "callback_data": f"sub_allot:{registrar}:{m.match.replace(':',' ')[:40]}"
                                        }])

                                    keyboard_inline.append([{
                                        "text": "🔔 None of these (Ignore these matches)",
                                        "callback_data": f"ignore_fuzz:{sub_id}"
                                    }])
                                    
                                    tg_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
                                    msg_text = (
                                        f"🔍 *Potential Match Found for Allotment Polling*\n\n"
                                        f"Requested: *{ipo_name}*\n"
                                        f"Registrar: *{registrar.upper()}*\n\n"
                                        f"We found potential match(es) listed on {registrar.upper()}.\n"
                                        f"Select a match below to check status immediately or ignore:"
                                    )
                                    payload = {
                                        "chat_id": chat_id,
                                        "text": msg_text,
                                        "parse_mode": "Markdown",
                                        "reply_markup": {"inline_keyboard": keyboard_inline}
                                    }
                                    resp = await session.post(tg_url, json=payload)
                                    if resp.status_code == 200:
                                        update_subscription_notified_matches(sub_id, list(current_match_names))
                        except Exception as fe:
                            logger.error(f"[POLL FUZZY ERROR] Failed fuzzy search for '{ipo_name}': {fe}")
                    continue

                # Allotment is available! Fetch status for all PANs
                tasks = [client.status_by_pan(session, pan=pan, company_name=ipo_name) for pan in pans]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                lines = [
                    f"*Allotment Status Announced!*\n",
                    f"*IPO:* {ipo_name}  *(Registrar: {registrar.upper()})*\n"
                ]
                for pan, res in zip(pans, results):
                    lines.append(format_allotment_result(pan, res))

                message_text = "\n".join(lines)

                # Send Telegram Notification
                tg_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
                payload = {
                    "chat_id": chat_id,
                    "text": message_text,
                    "parse_mode": "Markdown"
                }

                resp = await session.post(tg_url, json=payload)
                if resp.status_code == 200:
                    logger.info(f"[POLL SUCCESS] Alert delivered to chat_id {chat_id} for '{ipo_name}'.")
                    # Stop tracking this subscription by removing it from JSONBin
                    removed = remove_allotment_subscription(sub_id)
                    logger.info(f"[POLL CLEANUP] Removed subscription {sub_id}: {removed}")
                else:
                    logger.error(f"[POLL TG ERROR] Failed delivering alert to {chat_id}: {resp.status_code} - {resp.text}")

            except Exception as e:
                logger.error(f"[POLL EXCEPTION] Exception checking subscription '{sub_id}': {e}", exc_info=True)

            await asyncio.sleep(0.1)


if __name__ == "__main__":
    asyncio.run(process_subscriptions())
