from __future__ import annotations

import httpx

from . import RegistrarClient

import base64
from bs4 import BeautifulSoup
from captcha_solver import solve


class BigshareClient(RegistrarClient):
    """Client for Bigshare Services IPO allotment status (supports captcha)."""

    BASE = "https://ipo.bigshareonline.com/api"

    async def _get_captcha(self, session: httpx.AsyncClient):
        r = await session.get(f"{self.BASE}/GenerateCaptcha")
        r.raise_for_status()
        j = r.json()
        cid = j["CaptchaId"]
        img_b64 = j["CaptchaText"].split(",")[-1]
        return cid, base64.b64decode(img_b64)

    async def status_by_pan(self, session: httpx.AsyncClient, *, pan: str, ipo_code: str) -> str:  # noqa: D401
        # Try without captcha first
        quick_payload = {
            "IPOId": ipo_code,
            "PAN": pan.upper(),
            "CaptchaText": "",
            "CaptchaId": "",
        }
        try:
            resp0 = await session.post(f"{self.BASE}/GetApplicantAllotmentStatus", json=quick_payload)
            resp0.raise_for_status()
            d0 = resp0.json()
            if d0.get("Status"):
                html = d0.get("Result", "")
                return BeautifulSoup(html, "html.parser").get_text(" ", strip=True) or "No record found"
            if "captcha" not in d0.get("Message", "").lower():
                return d0.get("Message", "No record found")
        except Exception:
            pass  # fall through to captcha path

        # captcha path
        for _ in range(2):
            cid, img_bytes = await self._get_captcha(session)
            text = await solve(img_bytes)
            payload = {
                "IPOId": ipo_code,
                "PAN": pan.upper(),
                "CaptchaText": text,
                "CaptchaId": cid,
            }
            r = await session.post(f"{self.BASE}/GetApplicantAllotmentStatus", json=payload)
            r.raise_for_status()
            d = r.json()
            if d.get("Status"):
                html = d.get("Result", "")
                return BeautifulSoup(html, "html.parser").get_text(" ", strip=True) or "No record found"
            if "captcha" not in d.get("Message", "").lower():
                return d.get("Message", "No record found")
        return "Unable to solve captcha"
