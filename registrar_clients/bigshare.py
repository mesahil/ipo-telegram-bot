from __future__ import annotations

import httpx

from . import RegistrarClient

import logging
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class BigshareClient(RegistrarClient):
    """Client for Bigshare IPO allotment via FetchIpodetails; no captcha for PAN."""

    HOST = "https://ipo1.bigshareonline.com"

    async def _company_map(self, session: httpx.AsyncClient) -> dict[str, str]:
        """Scrape dropdown to build COMPANY_NAME -> code map."""
        url = f"{self.HOST}/ipo_status.html"
        logger.info("[REQUEST] GET %s", url)
        page = await session.get(url)
        page.raise_for_status()
        logger.info("[RESPONSE] GET %s | Status: %s | Length: %d bytes", url, page.status_code, len(page.text))
        soup = BeautifulSoup(page.text, "html.parser")
        sel = soup.find("select", id="ddlCompany")
        if not sel:
            logger.warning("[WARNING] Could not find dropdown ddlCompany on Bigshare page")
            return {}
        mapping: dict[str, str] = {}
        for opt in sel.find_all("option"):
            code = opt.get("value", "").strip()
            name = opt.text.strip().upper()
            if code and name:
                mapping[name] = code
        logger.info("[SUCCESS] Bigshare company map loaded: %d companies", len(mapping))
        return mapping

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
        }
        api_url = f"{self.HOST}/Data.aspx/FetchIpodetails"
        logger.info("[REQUEST] POST %s | CompanyCode=%s | PAN=%s", api_url, company_code, pan.upper())
        r = await session.post(api_url, json=payload)
        logger.info("[RESPONSE] POST %s | Status: %s", api_url, r.status_code)
        r.raise_for_status()
        html = r.json().get("d", "")
        name = allot = ""
        if isinstance(html, dict):
            name = html.get("Name", "")
            allot = html.get("ALLOTED", html.get("ALLOTED ", ""))
        else:
            txt = BeautifulSoup(str(html), "html.parser").get_text(" ", strip=True).upper()
            parts = txt.split()
            # crude extraction
            if "NAME" in parts and "ALLOTED" in parts:
                try:
                    name_idx = parts.index("NAME") + 1
                    allot_idx = parts.index("ALLOTED") + 1
                    name = parts[name_idx]
                    allot = parts[allot_idx]
                except Exception:
                    pass
        summary = f"Name: {name} | ALLOTED: {allot}" if name or allot else "No record found"
        return summary

    async def find_fuzzy_matches(self, session: httpx.AsyncClient, target: str, max_matches: int = 3):
        """
        Find fuzzy matches for a target company name.
        
        Args:
            session: HTTP session
            target: Target company name to match
            max_matches: Maximum number of fuzzy matches to return
            
        Returns:
            List of FuzzyMatch objects
        """
        from fuzzy_matcher import FuzzyMatcher
        
        cmap = await self._company_map(session)
        if not cmap:
            return []
        
        # Create fuzzy matcher
        matcher = FuzzyMatcher(confidence_threshold=0.5)  # Lower threshold for better recall
        
        # Find fuzzy matches
        matches = matcher.find_best_matches(target, cmap, max_matches=max_matches)
        
        print(f"Found {len(matches)} fuzzy matches for '{target}':")
        for match in matches:
            print(f"  - {match.match} (confidence: {match.confidence:.2f})")
        
        return matches
