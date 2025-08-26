from __future__ import annotations

import httpx

from bs4 import BeautifulSoup  # type: ignore

from . import RegistrarClient


class MufgClient(RegistrarClient):
    """Client for MUFG Intime (formerly Link-Intime)."""

    BASE = "https://in.mpms.mufg.com/Initial_Offer/public-issues"

    async def status_by_pan(self, session: httpx.AsyncClient, *, pan: str, ipo_code: str) -> str:  # noqa: D401
        payload = {"pan": pan.upper(), "companyName": ipo_code}
        resp = await session.post(f"{self.BASE}/getPanStatus", json=payload)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("status", False):
            return data.get("message", "No record found")
        # Sometimes the API wraps the HTML fragment inside allotmentStatus key.
        status = data.get("allotmentStatus") or data.get("panStatus") or "No data"
        # Remove any stray HTML tags if present.
        status_text = BeautifulSoup(status, "html.parser").get_text(" ", strip=True)
        return status_text
