from __future__ import annotations

import abc
from typing import Dict, Optional

import httpx


class RegistrarClient(abc.ABC):
    """Abstract interface every registrar wrapper must implement."""

    @abc.abstractmethod
    async def status_by_pan(self, session: httpx.AsyncClient, *, pan: str, ipo_code: str) -> str:  # pragma: no cover
        """Return allotment status text for the given PAN and IPO code."""

    # Optional helper – override if the registrar lets us enumerate IPOs.
    async def list_ipos(self, session: httpx.AsyncClient):  # pragma: no cover
        return []


# concrete client imports below – each must implement RegistrarClient
from .mufg import MufgClient  # noqa: E402  pylint: disable=wrong-import-position
from .mas import MasClient  # noqa: E402
from .kfin import KfinClient  # noqa: E402
from .bigshare import BigshareClient  # noqa: E402

_CLIENT_REGISTRY: Dict[str, type[RegistrarClient]] = {
    "mufg": MufgClient,
    "mas": MasClient,
    "kfin": KfinClient,
    "bigshare": BigshareClient,
}


def get_client_for_registrar(name: str) -> Optional[RegistrarClient]:
    """Return a new client instance for the given registrar short-name."""

    cls = _CLIENT_REGISTRY.get(name.lower())
    return cls() if cls else None
