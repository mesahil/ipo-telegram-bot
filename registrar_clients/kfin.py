from __future__ import annotations

import httpx

from . import RegistrarClient


class KfinClient(RegistrarClient):
    """Minimal stub – implement when KFin introduces new IPOs."""

    async def status_by_pan(self, session: httpx.AsyncClient, *, pan: str, ipo_code: str) -> str:  # noqa: D401
        return "KFin client not yet implemented"
