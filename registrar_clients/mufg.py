from __future__ import annotations

import httpx

from bs4 import BeautifulSoup  # type: ignore

from . import RegistrarClient


class MufgClient(RegistrarClient):
    """Client for MUFG Intime allotment status via IPO.aspx.* endpoints."""

    HOST = "https://in.mpms.mufg.com/Initial_Offer/IPO.aspx"

    async def _company_map(self, session: httpx.AsyncClient) -> dict[str, tuple[str, str]]:
        """Fetch list of active companies and return NAME -> (id)."""
        import xml.etree.ElementTree as ET
        r = await session.post(f"{self.HOST}/GetDetails", json={})
        r.raise_for_status()
        xml_str = r.json().get("d", "")
        root = ET.fromstring(xml_str) if xml_str else ET.Element("root")
        mapping: dict[str, str] = {}
        for node in root.findall(".//Table"):
            cid = node.findtext("company_id", "").strip()
            cname = node.findtext("companyname", "").upper().strip()
            if cid and cname:
                mapping[cname] = cid
        return mapping

    async def status_by_pan(
        self,
        session: httpx.AsyncClient,
        *,
        pan: str,
        ipo_code: str | None = None,
        company_name: str | None = None,
        confirmed_match: str | None = None,  # New parameter for confirmed fuzzy matches
    ) -> str:  # noqa: D401
        target = (company_name or ipo_code or "").upper()
        cmap = await self._company_map(session)
        print("target======>:", target)
        print("MUFG company map size:", len(cmap))

        def _norm(txt: str) -> str:
            return " ".join(
                txt.replace("LIMITED", "").replace("LTD", "").split()
            )

        match = None
        
        # If we have a confirmed match from fuzzy matching, use it directly
        if confirmed_match:
            confirmed_match_upper = confirmed_match.upper()
            if confirmed_match_upper in cmap:
                match = cmap[confirmed_match_upper]
                print(f"Using confirmed fuzzy match: {confirmed_match} -> {match}")
            else:
                # Try to find the confirmed match in the current cmap
                for name_key, company_id in cmap.items():
                    if _norm(confirmed_match_upper) == _norm(name_key):
                        match = company_id
                        print(f"Found confirmed match via normalization: {name_key} -> {match}")
                        break
        else:
            # Original exact/substring matching logic
            for name_key, company_id in cmap.items():
                if _norm(target) in _norm(name_key) or _norm(name_key) in _norm(target):
                    match = company_id
                    print(f"Found exact/substring match: {name_key} -> {match}")
                    break

        if not match:
            return "IPO not yet available on MUFG"
        
        print("MUFG match", match)
        company_id = match

        payload = {
            "clientid": company_id,
            "PAN": pan.upper(),
            "IFSC": "",
            "CHKVAL": "1",
            "token": "ZsJdrgsy5RiJbUHZPnJcCQ==",
        }

        print("MUFG payload", payload)

        r = await session.post(f"{self.HOST}/SearchOnPan", json=payload)
        r.raise_for_status()
        data = r.json().get("d", "")

        # If XML fragment – extract NAME1 and ALLOT
        if "<" in data:
            import xml.etree.ElementTree as ET

            root = ET.fromstring(data)
            table = root.find(".//Table")
            if table is not None:
                name = table.findtext("NAME1", "").strip()
                allotted = table.findtext("ALLOT", "").strip()
                return f"Name: {name} | ALLOTED: {allotted} shares"
            # Fallback to stripped text
            data = BeautifulSoup(data, "html.parser").get_text(" ", strip=True)

        return data or "No record found"

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
