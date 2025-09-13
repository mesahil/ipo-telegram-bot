from __future__ import annotations

import httpx

from . import RegistrarClient


class KfinClient(RegistrarClient):
    """Client for KFinTech IPO allotment – no captcha required for PAN queries."""

    BASE = "https://ipostatus.kfintech.com/api"

    async def get_ipo_list(self, session: httpx.AsyncClient) -> list:
        """Fetch the list of active IPOs from KFin."""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Referer": "https://ipostatus.kfintech.com/",
            "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "script",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Site": "same-origin"
        }
        
        try:
            # Fetch the JS bundle
            js_resp = await session.get(
                "https://ipostatus.kfintech.com/static/js/main.84ccbfb1.js", 
                headers=headers, 
                timeout=30
            )
            js_content = js_resp.text
            
            # Extract IPO list from rf=JSON.parse
            import re
            import json
            
            rf_pattern = r'rf=JSON\.parse\((\'[^\']*\')\)'
            rf_matches = re.findall(rf_pattern, js_content)
            
            if rf_matches:
                # Parse the IPO list
                json_str = rf_matches[0][1:-1]  # Remove quotes
                json_str = json_str.replace('\\"', '"').replace('\\\\', '\\')
                ipo_list = json.loads(json_str)
                return ipo_list
            
        except Exception as exc:
            print(f"Error fetching IPO list: {exc}")
        
        return []
    
    async def status_by_pan(
        self,
        session: httpx.AsyncClient,
        *,
        pan: str,
        ipo_code: str | None = None,
        company_name: str | None = None,
    ) -> str:  # noqa: D401
        # Get the IPO list
        ipo_list = await self.get_ipo_list(session)

        print("ipo_list ==========================================>", ipo_list)
        
        if not ipo_list:
            return "Unable to fetch IPO list"
        
        # If no IPO code provided, try to match by company name
        if not ipo_code and company_name:
            for ipo in ipo_list:
                if company_name.upper() in ipo['name'].upper():
                    ipo_code = ipo['clientId']
                    print(f"Matched: {ipo['name']} (ID: {ipo_code})")
                    break
        
        if not ipo_code:
            # Return list of available IPOs
            ipo_names = [f"• {ipo['name']}" for ipo in ipo_list]
            return "Available IPOs:\n" + "\n".join(ipo_names)

        payload = {
            "pan": pan.upper(),
            "ipoid": ipo_code or "",  # symbol or code
            "captcha": "",  # no captcha needed
            "captchaId": "",
        }

        # r = await session.post(f"{self.BASE}/GetPanStatus", json=payload)
        # r.raise_for_status()
        # data = r.json()
        # if not data.get("status"):
        #     return data.get("message", "No record found")

        # from bs4 import BeautifulSoup

        # html = data.get("result") or ""
        # text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
        return "No record found"
