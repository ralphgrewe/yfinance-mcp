from typing import Any
from typing import Protocol
from typing import runtime_checkable


class ProviderExhaustedError(Exception):
    """Raised when all providers have been tried and none returned data."""

    def __init__(self, method: str, providers_tried: list[str]) -> None:
        self.method = method
        self.providers_tried = providers_tried
        super().__init__(f"No provider returned data for '{method}'. Tried: {providers_tried}")


@runtime_checkable
class BaseProvider(Protocol):
    name: str  # "yfinance" | "onvista" | "eodhd"

    async def get_ticker_info(self, symbol: str) -> dict[str, Any] | None:
        """Returns normalized ticker info dict or None if unavailable."""
        ...

    async def get_price_history(
        self,
        symbol: str,
        period: str,
        interval: str,
    ) -> list[dict[str, Any]] | None:
        """Returns list of OHLCV dicts or None if unavailable."""
        ...

    async def get_financials(
        self,
        symbol: str,
        frequency: str,
    ) -> dict[str, Any] | None:
        """Returns income/balance/cashflow dict or None if unavailable."""
        ...

    async def search(self, query: str) -> list[dict[str, Any]] | None:
        """Returns list of matching instrument dicts or None."""
        ...
