from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from services.common.schemas.aircraft import StateVectorIn


class Source(ABC):
    """One external ADS-B provider. Implementations own their own rate
    limiting and circuit breaking; `poll_once` either returns the batch of
    state vectors it fetched or raises - callers decide what "raise" means
    (failover, backoff, log-and-continue).
    """

    name: str

    @abstractmethod
    async def poll_once(self) -> AsyncIterator[StateVectorIn]: ...

    @abstractmethod
    async def close(self) -> None: ...
