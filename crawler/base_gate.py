"""
Abstract base class for exchange API gates.

All gates return klines in a standardized format so the unified crawler
can process them identically regardless of exchange.
"""
from abc import ABC, abstractmethod
from typing import Any, List


class BaseGate(ABC):
    """Interface that every exchange gate must implement."""

    @abstractmethod
    async def get_klines(
        self,
        market_type: str,  # "spot" | "perp"
        params: dict,
        **kwargs: Any,
    ) -> List[list]:
        """
        Fetch klines from the exchange.

        params always contains: symbol, interval, startTime (ms), limit
        May contain: endTime (ms)

        Returns standardized rows, each a list:
            [open_time_ms, open, high, low, close, volume, quote_volume, trade_count]
        Rows are in ascending time order (oldest first).
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """Close underlying HTTP client."""
        ...

    @abstractmethod
    def max_batch_size(self, market_type: str) -> int:
        """Maximum number of klines per API call for this market type."""
        ...
