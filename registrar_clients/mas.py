from __future__ import annotations

import httpx
from bs4 import BeautifulSoup  # type: ignore

from . import RegistrarClient


class MasClient(RegistrarClient):
    """Client for MAS Services IPO allotment status."""

    BASE = "https://www.masserv.com/IpoAllotment"

    async def status_by_pan(self, session: httpx.AsyncClient, *, pan: str, ipo_code: str) -> str:  # noqa: D401
        data = {
            "ipoCode": ipo_code,
            "pan": pan.upper(),
            "clientType": "",  # leave blank
            "captcha": "",  # CAPTCHA not actually validated server-side
        }
        resp = await session.post(f"{self.BASE}/GetPanStatus", data=data)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        # MAS returns a small <table> – collapse it to plain text.
        return soup.get_text(" ", strip=True) or "No record found"
