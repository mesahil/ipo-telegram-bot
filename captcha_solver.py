from __future__ import annotations

import base64
import logging
import os
import re
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_OCR_INSTANCE = None


def _get_ocr():
    global _OCR_INSTANCE
    if _OCR_INSTANCE is None:
        try:
            import ddddocr
            _OCR_INSTANCE = ddddocr.DdddOcr(show_ad=False)
        except Exception as e:
            logger.warning("[CAPTCHA] ddddocr not available: %s", e)
            _OCR_INSTANCE = False
    return _OCR_INSTANCE if _OCR_INSTANCE is not False else None


GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"


async def solve(image_bytes: bytes, session: Optional[httpx.AsyncClient] = None) -> str:
    """Return 6-digit captcha text using local offline OCR (ddddocr) or Gemini Vision API.

    Args:
        image_bytes: Raw PNG/JPEG bytes of the captcha.
        session: Optional httpx.AsyncClient session.

    Returns:
        6-digit numeric string extracted from the image.
    """
    digits = ""
    ocr = _get_ocr()
    if ocr is not None:
        try:
            code = ocr.classification(image_bytes)
            digits = "".join(re.findall(r"\d+", code))
            if len(digits) == 6:
                logger.info("[CAPTCHA] Local OCR solved: %s", digits)
                return digits
            logger.warning("[CAPTCHA] Local OCR returned non-6 digits: '%s', checking fallback", code)
        except Exception as err:
            logger.warning("[CAPTCHA] Local OCR failed: %s, checking fallback", err)

    # Fallback to Gemini if GEMINI_API_KEY is configured
    from dotenv import load_dotenv
    load_dotenv()

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        if digits:
            return digits
        raise RuntimeError(
            "Captcha solving failed: Local OCR could not read 6 digits and GEMINI_API_KEY is not set in .env."
        )

    b64 = base64.b64encode(image_bytes).decode("utf-8")
    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": (
                            "Read the captcha text in this image. It consists of 6 digits. "
                            "Respond with ONLY the 6 digits, no spaces, no punctuation, no words."
                        )
                    },
                    {
                        "inline_data": {
                            "mime_type": "image/png",
                            "data": b64,
                        }
                    },
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.0,
            "maxOutputTokens": 10,
        },
    }

    url = f"{GEMINI_API_URL}?key={api_key}"

    if session is not None:
        resp = await session.post(url, json=payload, timeout=15.0)
    else:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload)

    resp.raise_for_status()
    data = resp.json()

    candidates = data.get("candidates", [])
    if not candidates:
        raise ValueError(f"No candidates returned by Gemini: {data}")

    text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "").strip()
    digits = "".join(re.findall(r"\d+", text))
    logger.info("[CAPTCHA] Gemini raw: '%s' -> extracted digits: '%s'", text, digits)
    return digits
