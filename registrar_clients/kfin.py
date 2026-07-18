from __future__ import annotations

import httpx

from . import RegistrarClient


import logging

logger = logging.getLogger(__name__)


class KfinClient(RegistrarClient):
    """Client for KFinTech IPO allotment – no captcha required for PAN queries."""

    BASE = "https://ipostatus.kfintech.com/api"

    async def get_ipo_list(self, session: httpx.AsyncClient) -> list:
        """Fetch the list of active IPOs from KFin."""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Referer": "https://ipostatus.kfintech.com/",
            "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "script",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Site": "same-origin"
        }
        
        try:
            url = "https://ipostatus.kfintech.com/"
            logger.info("[REQUEST] GET %s (Fetching dynamic main JS bundle)", url)
            html_resp = await session.get(url, headers=headers, timeout=30)
            html_content = html_resp.text
            logger.info("[RESPONSE] GET %s | Status: %s | Length: %d bytes", url, html_resp.status_code, len(html_content))

            import re
            script_match = re.search(r'<script[^>]+src="([^"]*main\.[^"]+\.js)"', html_content)

            if not script_match:
                logger.warning("[WARNING] Unable to locate main JS bundle on KFin IPO status page")
                return []

            js_path = script_match.group(1).lstrip("./")
            js_url = f"https://ipostatus.kfintech.com/{js_path}"

            logger.info("[REQUEST] GET %s (Fetching JS bundle)", js_url)
            js_resp = await session.get(js_url, headers=headers, timeout=30)
            js_content = js_resp.text
            logger.info("[RESPONSE] GET %s | Status: %s | Length: %d bytes", js_url, js_resp.status_code, len(js_content))
            
            import json
            rf_pattern = r'rf=JSON\.parse\((\'[^\']*\')\)'
            rf_matches = re.findall(rf_pattern, js_content)
            
            if rf_matches:
                json_str = rf_matches[0][1:-1]
                json_str = json_str.replace('\\"', '"').replace('\\\\', '\\')
                ipo_list = json.loads(json_str)
                logger.info("[SUCCESS] KFin active IPO list loaded: %d items", len(ipo_list))
                return ipo_list
            
        except Exception as exc:
            logger.error("[ERROR] Failed fetching KFin IPO list: %s", exc)
        
        return []
    
    async def status_by_pan(
        self,
        session: httpx.AsyncClient,
        *,
        pan: str,
        ipo_code: str | None = None,
        company_name: str | None = None,
        confirmed_match: str | None = None,
    ) -> str:
        ipo_list = await self.get_ipo_list(session)

        if not ipo_list:
            return "Unable to fetch IPO list"
        
        target_search = (confirmed_match or company_name or "").strip()

        if not ipo_code and target_search:
            cname_upper = target_search.upper()
            from fuzzy_matcher import FuzzyMatcher
            fm = FuzzyMatcher()
            norm_target = fm.normalize_name(cname_upper)

            for ipo in ipo_list:
                if isinstance(ipo, dict):
                    raw_name = str(ipo.get("name", ""))
                    ipo_name_upper = raw_name.upper()
                    norm_ipo_name = fm.normalize_name(ipo_name_upper)

                    if cname_upper == ipo_name_upper or norm_target == norm_ipo_name:
                        ipo_code = str(ipo.get("clientId") or "")
                        logger.info("[MATCH] KFin matched exact/normalized '%s' -> ID: %s", raw_name, ipo_code)
                        break
                    elif norm_target in norm_ipo_name or norm_ipo_name in norm_target:
                        ipo_code = str(ipo.get("clientId") or "")
                        logger.info("[MATCH] KFin matched substring '%s' -> ID: %s", raw_name, ipo_code)
                        break
        
        if not ipo_code:
            logger.info("[NOT FOUND] IPO '%s' not found on KFin active list", company_name)
            return "IPO not available on KFin"

        api_url = "https://0uz601ms56.execute-api.ap-south-1.amazonaws.com/prod/api/query?type=pan"

        headers = {
            "accept": "application/json, text/plain, */*",
            "origin": "https://ipostatus.kfintech.com",
            "referer": "https://ipostatus.kfintech.com/",
            "client_id": ipo_code or "",
            "reqparam": pan.upper(),
            "user-agent": "Mozilla/5.0",
        }

        logger.info("[REQUEST] GET %s | ClientID=%s | PAN=%s", api_url, ipo_code, pan.upper())
        r = await session.get(api_url, headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json()
        logger.info("[RESPONSE] GET %s | Status: %s | Data payload keys: %s", api_url, r.status_code, list(data.keys()) if isinstance(data, dict) else type(data))

        records = data.get("data") if isinstance(data, dict) else None
        if not records:
            if not data.get("status"):
                msg = data.get("message", "No record found")
                logger.info("[RESULT] KFin status response for PAN %s: %s", pan, msg)
                return msg

            html = data.get("result") or ""
            from bs4 import BeautifulSoup

            parsed = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
            res = parsed or "No record found"
            logger.info("[RESULT] KFin HTML status response for PAN %s: %s", pan, res)
            return res

        # Build human-readable response from records
        lines: list[str] = []
        for rec in records:
            if not isinstance(rec, dict):
                continue

            name = str(rec.get("Name", "")).title()
            allotted = rec.get("All_Shares") or rec.get("Allotted_Shares") or ""

            line_parts = [
                f"Name: {name}",
                f"Allotted: {allotted}",
            ]

            lines.append(" | ".join(part for part in line_parts if part))

        return "\n\n".join(lines) if lines else "No record found"

    async def find_fuzzy_matches(self, session: httpx.AsyncClient, target: str, max_matches: int = 5):
        """Find fuzzy matches or available active IPOs for a target company name on KFin."""
        from fuzzy_matcher import FuzzyMatcher, FuzzyMatch
        
        ipo_list = await self.get_ipo_list(session)
        if not ipo_list:
            return []
        
        candidates = {}
        for ipo in ipo_list:
            if isinstance(ipo, dict) and ipo.get("name"):
                candidates[str(ipo["name"])] = str(ipo.get("clientId") or "")
        
        matcher = FuzzyMatcher(confidence_threshold=0.3)
        matches = matcher.find_best_matches(target, candidates, max_matches=max_matches)
        
        if not matches and candidates:
            logger.info("[FUZZY] No direct threshold match for '%s', listing top available KFin IPOs as fallback", target)
            for name, cid in list(candidates.items())[:max_matches]:
                matches.append(FuzzyMatch(target=target, match=name, confidence=0.0, data=cid))
        
        logger.info("[FUZZY MATCH] Found %d match candidate(s) for '%s' on KFin", len(matches), target)
        return matches
