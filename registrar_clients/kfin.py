from __future__ import annotations

import httpx

from . import RegistrarClient


class KfinClient(RegistrarClient):
    """Client for KFinTech IPO allotment – requires captcha solving."""

    BASE = "https://ris.kfintech.com/api/ipostatus"

    async def _get_captcha(self, session: httpx.AsyncClient):
        """Return (captchaId, image-bytes)."""
        r = await session.get(f"{self.BASE}/GenerateCaptcha")
        r.raise_for_status()
        data = r.json()
        cid = data["captchaId"]
        img_b64 = data["captcha"].split(",")[-1]  # strip data URI header
        import base64

        img_bytes = base64.b64decode(img_b64)
        return cid, img_bytes

    async def status_by_pan(self, session: httpx.AsyncClient, *, pan: str, ipo_code: str) -> str:  # noqa: D401
        # First try without captcha – KFin sometimes disables it after allotment.
        initial_payload = {
            "pan": pan.upper(),
            "ipoid": ipo_code,
            "captcha": "",
            "captchaId": "",
        }
        try:
            r0 = await session.post(f"{self.BASE}/GetPanStatus", json=initial_payload)
            r0.raise_for_status()
            d0 = r0.json()
            if d0.get("status"):
                from bs4 import BeautifulSoup

                html0 = d0.get("result", "")
                return (
                    BeautifulSoup(html0, "html.parser").get_text(" ", strip=True)
                    or "No record found"
                )
            # If message says captcha required → fall through to captcha path
            if "captcha" not in (d0.get("message", "").lower()):
                return d0.get("message", "No record found")
        except Exception:
            pass  # fall back to captcha loop

        # Solve captcha path
        for attempt in range(2):
            captcha_id, img = await self._get_captcha(session)

            from captcha_solver import solve  # lazy import to avoid heavy deps if unused

            text = await solve(img)

            payload = {
                "pan": pan.upper(),
                "ipoid": ipo_code,  # they expect scrip_cd
                "captcha": text,
                "captchaId": captcha_id,
            }

            r = await session.post(f"{self.BASE}/GetPanStatus", json=payload)
            r.raise_for_status()
            data = r.json()
            if not data.get("status"):
                if attempt == 0:
                    # retry once with new captcha
                    continue
                return data.get("message", "Captcha failed")

            # KFin returns HTML fragment in result field
            from bs4 import BeautifulSoup

            html = data.get("result") or ""
            status = (
                BeautifulSoup(html, "html.parser").get_text(" ", strip=True) or "No record found"
            )
            return status

        return "Unable to solve captcha"
