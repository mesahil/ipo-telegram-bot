from __future__ import annotations

import httpx

from bs4 import BeautifulSoup  # type: ignore

from . import RegistrarClient


import logging

logger = logging.getLogger(__name__)


class MufgClient(RegistrarClient):
    """Client for MUFG Intime allotment status via IPO.aspx.* endpoints."""

    HOST = "https://in.mpms.mufg.com/Initial_Offer/IPO.aspx"

    async def _company_map(self, session: httpx.AsyncClient) -> dict[str, tuple[str, str]]:
        """Fetch list of active companies and return NAME -> (id)."""
        import xml.etree.ElementTree as ET
        url = f"{self.HOST}/GetDetails"
        logger.info("[REQUEST] POST %s", url)
        r = await session.post(url, json={})
        r.raise_for_status()
        logger.info("[RESPONSE] POST %s | Status: %s", url, r.status_code)
        xml_str = r.json().get("d", "")
        root = ET.fromstring(xml_str) if xml_str else ET.Element("root")
        mapping: dict[str, str] = {}
        for node in root.findall(".//Table"):
            cid = node.findtext("company_id", "").strip()
            cname = node.findtext("companyname", "").upper().strip()
            if cid and cname:
                mapping[cname] = cid
        logger.info("[SUCCESS] MUFG company map loaded: %d companies", len(mapping))
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
        target = (company_name or ipo_code or "").upper()
        cmap = await self._company_map(session)

        def _norm(txt: str) -> str:
            return " ".join(
                txt.replace("LIMITED", "").replace("LTD", "").split()
            )

        match = None
        
        if confirmed_match:
            confirmed_match_upper = confirmed_match.upper()
            if confirmed_match_upper in cmap:
                match = cmap[confirmed_match_upper]
                logger.info("[MATCH] MUFG matched confirmed fuzzy match '%s' -> ID: %s", confirmed_match, match)
            else:
                for name_key, company_id in cmap.items():
                    if _norm(confirmed_match_upper) == _norm(name_key):
                        match = company_id
                        logger.info("[MATCH] MUFG normalized fuzzy match '%s' -> ID: %s", name_key, match)
                        break
        else:
            for name_key, company_id in cmap.items():
                if _norm(target) in _norm(name_key) or _norm(name_key) in _norm(target):
                    match = company_id
                    logger.info("[MATCH] MUFG exact/substring match '%s' -> ID: %s", name_key, match)
                    break

        if not match:
            logger.warning("[NOT FOUND] IPO '%s' not found on MUFG", target)
            return "IPO not yet available on MUFG"
        
        company_id = match

        payload = {
            "clientid": company_id,
            "PAN": pan.upper(),
            "IFSC": "",
            "CHKVAL": "1",
            "token": "ZsJdrgsy5RiJbUHZPnJcCQ==",
        }

        api_url = f"{self.HOST}/SearchOnPan"
        logger.info("[REQUEST] POST %s | ClientID=%s | PAN=%s", api_url, company_id, pan.upper())
        r = await session.post(api_url, json=payload)
        logger.info("[RESPONSE] POST %s | Status: %s", api_url, r.status_code)
        r.raise_for_status()
        data = r.json().get("d", "")

        if "<" in data:
            import xml.etree.ElementTree as ET

            root = ET.fromstring(data)
            table = root.find(".//Table")
            if table is not None:
                name = table.findtext("NAME1", "").strip()
                allotted = table.findtext("ALLOT", "").strip()
                res = f"Name: {name} | ALLOTED: {allotted} shares"
                logger.info("[RESULT] MUFG status response for PAN %s: %s", pan, res)
                return res
            data = BeautifulSoup(data, "html.parser").get_text(" ", strip=True)

        res = data or "No record found"
        logger.info("[RESULT] MUFG status response for PAN %s: %s", pan, res)
        return res

    async def find_fuzzy_matches(self, session: httpx.AsyncClient, target: str, max_matches: int = 3):
        from fuzzy_matcher import FuzzyMatcher
        
        cmap = await self._company_map(session)
        if not cmap:
            return []
        
        matcher = FuzzyMatcher(confidence_threshold=0.5)
        matches = matcher.find_best_matches(target, cmap, max_matches=max_matches)
        
        logger.info("[FUZZY MATCH] Found %d fuzzy match(es) for '%s'", len(matches), target)
        for m in matches:
            logger.info("  - Candidate: %s (Confidence: %.2f)", m.match, m.confidence)
        
        return matches
