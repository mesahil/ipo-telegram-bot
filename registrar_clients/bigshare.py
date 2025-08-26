from __future__ import annotations

import httpx

from . import RegistrarClient


class BigshareClient(RegistrarClient):
    """Minimal stub – implement when Bigshare IPOs required."""

    async def status_by_pan(self, session: httpx.AsyncClient, *, pan: str, ipo_code: str) -> str:  # noqa: D401
        return "Bigshare client not yet implemented"
