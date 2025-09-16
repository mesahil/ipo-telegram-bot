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
            # Fetch the home page to discover the current JS bundle URL
            import re
            html_resp = await session.get(
                "https://ipostatus.kfintech.com/",
                headers=headers,
                timeout=30,
            )
            html_content = html_resp.text
            print("html_content ==========================================>", html_content)

            # Look for the script tag with the main.*.js bundle
            script_match = re.search(r'<script[^>]+src="([^"]*main\.[^"]+\.js)"', html_content)

            if not script_match:
                print("Unable to locate main JS bundle on KFin IPO status page")
                return html_content

            js_path = script_match.group(1).lstrip("./")
            js_url = f"https://ipostatus.kfintech.com/{js_path}"

            # Fetch the JS bundle now that we have its dynamic path
            js_resp = await session.get(
                js_url,
                headers=headers,
                timeout=30,
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

        # New KFin backend endpoint (AWS API Gateway)
        api_url = "https://0uz601ms56.execute-api.ap-south-1.amazonaws.com/prod/api/query?type=pan"

        headers = {
            "accept": "application/json, text/plain, */*",
            "origin": "https://ipostatus.kfintech.com",
            "referer": "https://ipostatus.kfintech.com/",
            "client_id": ipo_code or "",
            "reqparam": pan.upper(),
            "user-agent": "Mozilla/5.0",  # minimal UA
        }

        r = await session.get(api_url, headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json()
        if not data.get("status"):
            return data.get("message", "No record found")

        from bs4 import BeautifulSoup

        html = data.get("result") or ""
        text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
        return "No record found"
