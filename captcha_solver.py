import base64
import os
import asyncio
from typing import Any

from PIL import Image
import google.generativeai as genai

# Configure Gemini API
_API_KEY = os.getenv("GEMINI_API_KEY")
if not _API_KEY:
    raise RuntimeError("GEMINI_API_KEY environment variable not set")

genai.configure(api_key=_API_KEY)
_MODEL = genai.GenerativeModel("gemini-pro-vision")


async def solve(image_bytes: bytes) -> str:
    """Return captcha text recognised by Gemini.

    Args:
        image_bytes: Raw PNG/JPEG bytes of the captcha.

    Returns:
        Upper-cased alphanumeric text extracted from the image.
    """
    # Gemini requires base64 encoded inline data for images
    b64 = base64.b64encode(image_bytes).decode()
    content: dict[str, Any] = {
        "parts": [
            {
                "text": (
                    "Read the text in this captcha image. "
                    "Respond with ONLY the captcha text, no extra words."
                )
            },
            {"inline_data": {"mime_type": "image/png", "data": b64}},
        ]
    }

    loop = asyncio.get_running_loop()
    response = await loop.run_in_executor(None, _MODEL.generate_content, content)
    text: str = response.text.strip().split()[0]  # use first token only
    return text.upper()  # standardise
