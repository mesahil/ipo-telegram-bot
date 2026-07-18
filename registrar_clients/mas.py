import logging
import httpx
from bs4 import BeautifulSoup

from . import RegistrarClient

logger = logging.getLogger(__name__)


class MasClient(RegistrarClient):
    """Client for MAS Services IPO allotment status."""

    BASE = "https://www.masserv.com/IpoAllotment"

    async def status_by_pan(self, session: httpx.AsyncClient, *, pan: str, ipo_code: str) -> str:
        data = {
            "ipoCode": ipo_code,
            "pan": pan.upper(),
            "clientType": "",
            "captcha": "",
        }
        url = f"{self.BASE}/GetPanStatus"
        logger.info("[REQUEST] POST %s | ipoCode=%s | PAN=%s", url, ipo_code, pan.upper())
        resp = await session.post(url, data=data)
        logger.info("[RESPONSE] POST %s | Status: %s", url, resp.status_code)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        res = soup.get_text(" ", strip=True) or "No record found"
        logger.info("[RESULT] MAS status response for PAN %s: %s", pan, res)
        return res
