from __future__ import annotations

import asyncio
import base64
import logging
from bs4 import BeautifulSoup
import httpx

from . import RegistrarClient
import captcha_solver

logger = logging.getLogger(__name__)


class BigshareClient(RegistrarClient):
    """Client for Bigshare IPO allotment via FetchIpodetails with captcha handling."""

    SERVERS = [
        "https://ipo.bigshareonline.com",
        "https://ipo1.bigshareonline.com",
        "https://ipo2.bigshareonline.com",
    ]
    HOST = "https://ipo1.bigshareonline.com"

    _lock = asyncio.Lock()
    _server_idx = 0

    @classmethod
    def _next_server(cls) -> str:
        server = cls.SERVERS[cls._server_idx % len(cls.SERVERS)]
        cls._server_idx += 1
        return server

    async def _company_map(self, session: httpx.AsyncClient) -> dict[str, str]:
        """Scrape dropdown to build COMPANY_NAME -> code map."""
        for server in self.SERVERS:
            url = f"{server}/ipo_status.html"
            try:
                logger.info("[REQUEST] GET %s", url)
                page = await session.get(url, timeout=10.0)
                if page.status_code != 200:
                    continue
                soup = BeautifulSoup(page.text, "html.parser")
                sel = soup.find("select", id="ddlCompany")
                if not sel:
                    continue
                mapping: dict[str, str] = {}
                for opt in sel.find_all("option"):
                    code = opt.get("value", "").strip()
                    name = opt.text.strip().upper()
                    if code and name:
                        mapping[name] = code
                if mapping:
                    logger.info("[SUCCESS] Bigshare company map loaded from %s: %d companies", server, len(mapping))
                    return mapping
            except Exception as e:
                logger.warning("[WARNING] Failed to load company map from %s: %s", server, e)

        return {}

    async def status_by_pan(
        self,
        session: httpx.AsyncClient,
        *,
        pan: str,
        ipo_code: str | None = None,
        company_name: str | None = None,
        confirmed_match: str | None = None,
    ) -> str:
        target_name = (company_name or ipo_code or "").strip()
        cmap = await self._company_map(session)

        def _normalize(txt: str) -> str:
            return " ".join(
                txt.replace("LIMITED", "").replace("LTD", "").replace("&", "AND").split()
            )

        company_code = None

        if confirmed_match:
            confirmed_match_upper = confirmed_match.upper()
            if confirmed_match_upper in cmap:
                company_code = cmap[confirmed_match_upper]
                logger.info("[MATCH] Bigshare matched confirmed fuzzy match '%s' -> Code: %s", confirmed_match, company_code)
            else:
                for name_key, code_val in cmap.items():
                    if _normalize(confirmed_match_upper) == _normalize(name_key):
                        company_code = code_val
                        logger.info("[MATCH] Bigshare normalized fuzzy match '%s' -> Code: %s", name_key, company_code)
                        break
        else:
            key = target_name.upper()
            company_code = cmap.get(key)
            if company_code:
                logger.info("[MATCH] Bigshare exact match '%s' -> Code: %s", key, company_code)
            else:
                normalized_target = _normalize(key)
                for name_key, code_val in cmap.items():
                    if _normalize(name_key) == normalized_target:
                        company_code = code_val
                        logger.info("[MATCH] Bigshare normalized match '%s' -> Code: %s", name_key, company_code)
                        break

            if not company_code:
                for name_key, code_val in cmap.items():
                    if key in name_key or name_key in key:
                        company_code = code_val
                        logger.info("[MATCH] Bigshare substring match '%s' -> Code: %s", name_key, company_code)
                        break

        if not company_code:
            logger.warning("[NOT FOUND] IPO '%s' not found on Bigshare", target_name)
            return "IPO not yet available on Bigshare"

        # Serialize requests and rotate servers to prevent 429 Too Many Requests
        async with self._lock:
            await asyncio.sleep(0.4)

            max_retries = 3
            for attempt in range(1, max_retries + 1):
                server = self._next_server()
                captcha_url = f"{server}/Captcha.ashx"
                logger.info("[REQUEST] GET %s | PAN=%s (attempt %d/%d)", captcha_url, pan.upper(), attempt, max_retries)

                try:
                    c_resp = await session.get(captcha_url, timeout=10.0)
                    if c_resp.status_code == 429:
                        logger.warning("[THROTTLE] Bigshare returned 429 on %s, backing off...", server)
                        await asyncio.sleep(0.8)
                        continue
                    c_resp.raise_for_status()
                    c_data = c_resp.json()
                except Exception as exc:
                    logger.warning("[WARNING] Failed to fetch captcha from %s: %s", server, exc)
                    await asyncio.sleep(0.5)
                    continue

                token = c_data.get("token") or c_data.get("Token", "")
                img_b64 = c_data.get("image") or c_data.get("Image", "")
                if not token or not img_b64:
                    logger.warning("[WARNING] Invalid captcha payload from Bigshare: %s", c_data)
                    continue

                if "," in img_b64:
                    img_b64 = img_b64.split(",", 1)[1]

                try:
                    image_bytes = base64.b64decode(img_b64)
                    captcha_code = await captcha_solver.solve(image_bytes, session=session)
                except Exception as exc:
                    logger.error("[ERROR] Captcha solver failed on attempt %d: %s", attempt, exc)
                    if attempt == max_retries:
                        return f"Error solving Bigshare captcha: {exc}"
                    continue

                payload = {
                    "Applicationno": "",
                    "Company": company_code,
                    "SelectionType": "PN",
                    "PanNo": pan.upper(),
                    "txtcsdl": "",
                    "txtDPID": "",
                    "txtClId": "",
                    "ddlType": "0",
                    "lang": "en",
                    "CaptchaToken": token,
                    "CaptchaAnswer": captcha_code,
                    "ResultToken": "",
                }
                api_url = f"{server}/Data.aspx/FetchIpodetails"
                logger.info(
                    "[REQUEST] POST %s | CompanyCode=%s | PAN=%s | Captcha=%s (attempt %d)",
                    api_url,
                    company_code,
                    pan.upper(),
                    captcha_code,
                    attempt,
                )

                try:
                    r = await session.post(api_url, json=payload, timeout=15.0)
                    logger.info("[RESPONSE] POST %s | Status: %s", api_url, r.status_code)
                except Exception as exc:
                    logger.warning("[WARNING] Failed calling FetchIpodetails on %s: %s", server, exc)
                    await asyncio.sleep(0.5)
                    continue

                if r.status_code == 429:
                    logger.warning("[THROTTLE] FetchIpodetails returned 429 on %s, backing off...", server)
                    await asyncio.sleep(0.8)
                    continue

                if r.status_code != 200:
                    logger.warning("[WARNING] FetchIpodetails returned status %s: %s", r.status_code, r.text)
                    continue

                data = r.json().get("d", {})
                if isinstance(data, dict):
                    status_val = data.get("Status", "")
                    if status_val == "CAPTCHA":
                        logger.warning("[CAPTCHA] Bigshare rejected captcha '%s', retrying...", captcha_code)
                        continue

                    if status_val == "NOTFOUND":
                        logger.info("[RESULT] Bigshare status for PAN %s: No record found", pan.upper())
                        return "No record found"

                    records = data.get("Records") or []
                    if records and isinstance(records, list):
                        lines = []
                        for rec in records:
                            rec_name = rec.get("Name", "").strip()
                            rec_allot = rec.get("ALLOTED", "").strip()
                            rec_applied = rec.get("APPLIED", "").strip()
                            line = f"Name: {rec_name} | ALLOTED: {rec_allot}"
                            if rec_applied:
                                line += f" | Applied: {rec_applied}"
                            lines.append(line)
                        return "\n\n".join(lines)

                    name = data.get("Name", "").strip()
                    allot = data.get("ALLOTED", "").strip()
                    applied = data.get("APPLIED", "").strip()
                    if name or allot:
                        res = f"Name: {name} | ALLOTED: {allot}"
                        if applied:
                            res += f" | Applied: {applied}"
                        return res

                    msg = data.get("Message", "").strip()
                    if msg:
                        if any(nf in msg.lower() for nf in ["not found", "no data found", "no record"]):
                            return "No record found"
                        return msg
                    return "No record found"
                else:
                    txt = BeautifulSoup(str(data), "html.parser").get_text(" ", strip=True)
                    return txt or "No record found"

            return "Could not verify Bigshare allotment (captcha attempts exceeded)"

    async def find_fuzzy_matches(self, session: httpx.AsyncClient, target: str, max_matches: int = 3):
        """Find fuzzy matches for a target company name."""
        from fuzzy_matcher import FuzzyMatcher

        cmap = await self._company_map(session)
        if not cmap:
            return []

        matcher = FuzzyMatcher(confidence_threshold=0.5)
        matches = matcher.find_best_matches(target, cmap, max_matches=max_matches)

        logger.info("[FUZZY] Found %d fuzzy matches for '%s':", len(matches), target)
        for match in matches:
            logger.info("  - %s (confidence: %.2f)", match.match, match.confidence)

        return matches
