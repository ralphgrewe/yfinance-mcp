"""Tests for ProviderRegistry: fallback chain, direct selection, exhausted error."""

import pytest

from yfmcp.providers.base import ProviderExhaustedError
from yfmcp.providers.registry import ProviderRegistry
from yfmcp.providers.registry import _looks_like_wkn


class _OkProvider:
    """Always returns data."""

    def __init__(self, name: str, data: object) -> None:
        self.name = name
        self._data = data

    async def get_ticker_info(self, symbol: str):
        return self._data

    async def get_price_history(self, symbol: str, period: str, interval: str, prepost: bool = False):
        return self._data

    async def get_financials(self, symbol: str, frequency: str):
        return self._data

    async def search(self, query: str):
        return self._data


class _NoneProvider:
    """Always returns None (no data for this provider)."""

    def __init__(self, name: str) -> None:
        self.name = name

    async def get_ticker_info(self, symbol: str):
        return None

    async def get_price_history(self, symbol: str, period: str, interval: str, prepost: bool = False):
        return None

    async def get_financials(self, symbol: str, frequency: str):
        return None

    async def search(self, query: str):
        return None


class _RaisingProvider:
    """Always raises an exception."""

    def __init__(self, name: str) -> None:
        self.name = name

    async def get_ticker_info(self, symbol: str):
        raise RuntimeError("network error")

    async def get_price_history(self, symbol: str, period: str, interval: str, prepost: bool = False):
        raise RuntimeError("network error")

    async def get_financials(self, symbol: str, frequency: str):
        raise RuntimeError("network error")

    async def search(self, query: str):
        raise RuntimeError("network error")


# ---------------------------------------------------------------------------
# Fallback chain tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_first_provider_succeeds() -> None:
    """When first provider returns data, it is used without trying others."""
    p1 = _OkProvider("p1", {"symbol": "AAPL"})
    p2 = _OkProvider("p2", {"symbol": "SHOULD_NOT_BE_USED"})
    registry = ProviderRegistry([p1, p2])

    result, provider_name = await registry.get("get_ticker_info", "auto", symbol="AAPL")

    assert result == {"symbol": "AAPL"}
    assert provider_name == "p1"


@pytest.mark.asyncio
async def test_auto_falls_back_when_first_returns_none() -> None:
    """When first provider returns None, registry tries next provider."""
    p1 = _NoneProvider("p1")
    p2 = _OkProvider("p2", {"symbol": "AJ91.F"})
    registry = ProviderRegistry([p1, p2])

    result, provider_name = await registry.get("get_ticker_info", "auto", symbol="AJ91.F")

    assert result == {"symbol": "AJ91.F"}
    assert provider_name == "p2"


@pytest.mark.asyncio
async def test_auto_falls_back_when_first_raises() -> None:
    """When first provider raises, it is treated as None and next is tried."""
    p1 = _RaisingProvider("p1")
    p2 = _OkProvider("p2", {"symbol": "AJ91.F"})
    registry = ProviderRegistry([p1, p2])

    result, provider_name = await registry.get("get_ticker_info", "auto", symbol="AJ91.F")

    assert result == {"symbol": "AJ91.F"}
    assert provider_name == "p2"


@pytest.mark.asyncio
async def test_auto_raises_exhausted_when_all_return_none() -> None:
    """ProviderExhaustedError is raised when all providers return None."""
    p1 = _NoneProvider("yfinance")
    p2 = _NoneProvider("onvista")
    registry = ProviderRegistry([p1, p2])

    with pytest.raises(ProviderExhaustedError) as exc_info:
        await registry.get("get_ticker_info", "auto", symbol="UNKNOWN")

    assert exc_info.value.providers_tried == ["yfinance", "onvista"]
    assert "get_ticker_info" in str(exc_info.value)


@pytest.mark.asyncio
async def test_auto_raises_exhausted_when_all_raise() -> None:
    """ProviderExhaustedError is raised when all providers raise exceptions."""
    p1 = _RaisingProvider("yfinance")
    p2 = _RaisingProvider("onvista")
    registry = ProviderRegistry([p1, p2])

    with pytest.raises(ProviderExhaustedError) as exc_info:
        await registry.get("get_ticker_info", "auto", symbol="UNKNOWN")

    assert set(exc_info.value.providers_tried) == {"yfinance", "onvista"}


# ---------------------------------------------------------------------------
# Direct source selection tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_direct_source_uses_only_named_provider() -> None:
    """When source is specified, only that provider is queried."""
    p1 = _OkProvider("yfinance", {"symbol": "AAPL"})
    p2 = _OkProvider("onvista", {"symbol": "AJ91.F"})
    registry = ProviderRegistry([p1, p2])

    result, provider_name = await registry.get("get_ticker_info", "onvista", symbol="AJ91.F")

    assert result == {"symbol": "AJ91.F"}
    assert provider_name == "onvista"


@pytest.mark.asyncio
async def test_direct_source_raises_exhausted_when_provider_returns_none() -> None:
    """Direct source selection does NOT fall back when provider returns None."""
    p1 = _OkProvider("yfinance", {"symbol": "AAPL"})
    p2 = _NoneProvider("onvista")
    registry = ProviderRegistry([p1, p2])

    with pytest.raises(ProviderExhaustedError) as exc_info:
        await registry.get("get_ticker_info", "onvista", symbol="AJ91.F")

    assert exc_info.value.providers_tried == ["onvista"]


@pytest.mark.asyncio
async def test_direct_source_raises_exhausted_for_unknown_provider() -> None:
    """Requesting a provider not in the registry raises ProviderExhaustedError."""
    p1 = _OkProvider("yfinance", {"symbol": "AAPL"})
    registry = ProviderRegistry([p1])

    with pytest.raises(ProviderExhaustedError) as exc_info:
        await registry.get("get_ticker_info", "eodhd", symbol="AAPL")

    assert "eodhd" in exc_info.value.providers_tried


# ---------------------------------------------------------------------------
# WKN routing
# ---------------------------------------------------------------------------


def test_looks_like_wkn_valid() -> None:
    for wkn in ["BAY001", "BASF11", "503750", "A1EWWW", "723610", "ENAG99"]:
        assert _looks_like_wkn(wkn), f"{wkn} should be detected as WKN"


def test_looks_like_wkn_invalid() -> None:
    for sym in ["AAPL", "SAP", "SAP.DE", "DE0007164600", "GOOGL", "MSFT", "BAZFOO.F", "AJ91.F"]:
        assert not _looks_like_wkn(sym), f"{sym} should NOT be detected as WKN"


@pytest.mark.asyncio
async def test_wkn_symbol_routes_onvista_first() -> None:
    """When auto mode receives a WKN, onvista is tried before yfinance."""
    tried_order: list[str] = []

    class _TrackingProvider:
        def __init__(self, name: str) -> None:
            self.name = name

        async def get_ticker_info(self, symbol: str):
            tried_order.append(self.name)
            return None  # let fallback continue

    yf = _TrackingProvider("yfinance")
    ov = _TrackingProvider("onvista")
    registry = ProviderRegistry([yf, ov])  # default order: yfinance first

    with pytest.raises(ProviderExhaustedError):
        await registry.get("get_ticker_info", "auto", symbol="BAY001")

    assert tried_order[0] == "onvista", "onvista should be tried first for WKN symbols"


@pytest.mark.asyncio
async def test_non_wkn_symbol_routes_yfinance_first() -> None:
    """For regular ticker symbols, yfinance (default first provider) is tried first."""
    tried_order: list[str] = []

    class _TrackingProvider:
        def __init__(self, name: str) -> None:
            self.name = name

        async def get_ticker_info(self, symbol: str):
            tried_order.append(self.name)
            return None

    yf = _TrackingProvider("yfinance")
    ov = _TrackingProvider("onvista")
    registry = ProviderRegistry([yf, ov])

    with pytest.raises(ProviderExhaustedError):
        await registry.get("get_ticker_info", "auto", symbol="AAPL")

    assert tried_order[0] == "yfinance", "yfinance should be tried first for regular ticker symbols"


# ---------------------------------------------------------------------------
# Kwargs forwarding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kwargs_forwarded_to_provider() -> None:
    """Registry correctly forwards keyword arguments to provider methods."""
    received: dict = {}

    class _RecordingProvider:
        name = "recorder"

        async def get_price_history(self, symbol: str, period: str, interval: str, prepost: bool = False):
            received.update({"symbol": symbol, "period": period, "interval": interval, "prepost": prepost})
            return [{"Close": 100.0}]

    registry = ProviderRegistry([_RecordingProvider()])
    await registry.get("get_price_history", "auto", symbol="AAPL", period="1mo", interval="1d", prepost=True)

    assert received == {"symbol": "AAPL", "period": "1mo", "interval": "1d", "prepost": True}
