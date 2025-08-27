from __future__ import annotations

import httpx

from . import RegistrarClient


class BigshareClient(RegistrarClient):
    """Minimal stub – implement when Bigshare IPOs required."""

    async def status_by_pan(self, session: httpx.AsyncClient, *, pan: str, ipo_code: str) -> str:  # noqa: D401
        # Shortcut: try once without captcha – Bigshare sometimes removes it.
        short = {
            "IPOId": ipo_code,
            "PAN": pan.upper(),
            "CaptchaText": "",
            "CaptchaId": "",
        }
        try:
            r0 = await session.post(f"{self.BASE}/GetApplicantAllotmentStatus", json=short)
            r0.raise_for_status()
            d0 = r0.json()
            if d0.get("Status"):
                html0 = d0.get("Result", "")
                return (
                    BeautifulSoup(html0, "html.parser").get_text(" ", strip=True)
                    or "No record found"
                )
            if "captcha" not in d0.get("Message", "").lower():
                return d0.get("Message", "No record found")
        except Exception:
            pass

        # Captcha-required path
        for attempt in range(2):
