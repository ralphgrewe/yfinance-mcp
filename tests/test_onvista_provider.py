"""Tests for OnvistaProvider: field mapping, ISIN lookup, None on missing symbol."""

from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from yfmcp.providers.onvista_provider import OnvistaProvider


def _make_instrument(
    symbol: str = "AJ91",
    name: str = "DocCheck AG",
    isin: str = "DE000A1A6WE6",
    inst_type: str = "STOCK",
    close: float = 12.50,
    currency: str = "EUR",
    snapshot: dict | None = None,
) -> MagicMock:
    """Build a mock pyonvista Instrument with the given attributes."""
    instrument = MagicMock()
    instrument.symbol = symbol
    instrument.name = name
    instrument.isin = isin
    instrument.type = inst_type
    instrument._snapshot_json = snapshot or {}

    # quote
    quote = MagicMock()
    quote.close = close
    instrument.quote = quote

    # notation
    notation = MagicMock()
    notation.currency = currency
    instrument.notations = [notation]

    # Financial ratios
    from pyonvista.api import FinancialRatios, CompanyInfo

    ratios = FinancialRatios(
        pe_ratio=18.2,
        pb_ratio=2.1,
        eps=0.68,
        dividend_yield=0.032,
        market_cap=87_500_000,
    )
    instrument.get_financial_ratios.return_value = ratios

    company = CompanyInfo(
        sector="Healthcare",
        industry="Health Information Services",
        country="DE",
        employees=420,
        website="https://doccheck.com",
        headquarters="XETRA",
    )
    instrument.get_company_info.return_value = company

    return instrument


@pytest.fixture()
def provider() -> OnvistaProvider:
    return OnvistaProvider(request_delay=0.0)


# ---------------------------------------------------------------------------
# _ensure_client / session bootstrap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_client_creates_session_once(provider: OnvistaProvider) -> None:
    """_ensure_client returns the same API instance on repeated calls."""
    with patch("yfmcp.providers.onvista_provider.aiohttp.ClientSession") as mock_session_cls:
        mock_session = AsyncMock()
        mock_session.closed = False
        mock_session_cls.return_value = mock_session

        with patch("yfmcp.providers.onvista_provider.PyOnVista") as mock_api_cls:
            mock_api = AsyncMock()
            mock_api_cls.return_value = mock_api

            api1 = await provider._ensure_client()
            api2 = await provider._ensure_client()

    assert api1 is api2
    mock_api_cls.assert_called_once()


# ---------------------------------------------------------------------------
# get_ticker_info
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_ticker_info_returns_normalized_fields(provider: OnvistaProvider) -> None:
    """get_ticker_info maps onvista fields to the normalized schema."""
    instrument = _make_instrument()

    with patch.object(provider, "_resolve_instrument", AsyncMock(return_value=instrument)):
        result = await provider.get_ticker_info("AJ91.F")

    assert result is not None
    assert result["symbol"] == "AJ91"
    assert result["isin"] == "DE000A1A6WE6"
    assert result["name"] == "DocCheck AG"
    assert result["currentPrice"] == 12.50
    assert result["currency"] == "EUR"
    assert result["trailingPE"] == 18.2
    assert result["priceToBook"] == 2.1
    assert result["trailingEps"] == 0.68
    assert result["dividendYield"] == 0.032
    assert result["marketCap"] == 87_500_000
    assert result["sector"] == "Healthcare"
    assert result["industry"] == "Health Information Services"
    assert result["country"] == "DE"
    assert result["employees"] == 420
    assert "_provider" not in result  # _provider is injected by the registry/server layer, not the provider


@pytest.mark.asyncio
async def test_get_ticker_info_returns_none_when_symbol_not_found(provider: OnvistaProvider) -> None:
    """get_ticker_info returns None when _resolve_instrument returns None."""
    with patch.object(provider, "_resolve_instrument", AsyncMock(return_value=None)):
        result = await provider.get_ticker_info("UNKNOWN_XYZ")

    assert result is None


@pytest.mark.asyncio
async def test_get_ticker_info_returns_none_on_exception(provider: OnvistaProvider) -> None:
    """get_ticker_info returns None when an exception occurs."""
    with patch.object(provider, "_resolve_instrument", AsyncMock(side_effect=RuntimeError("network"))):
        result = await provider.get_ticker_info("AJ91.F")

    assert result is None


# ---------------------------------------------------------------------------
# _resolve_instrument: ISIN path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_instrument_uses_isin_directly(provider: OnvistaProvider) -> None:
    """A 12-char ISIN bypasses search and calls request_instrument(isin=...)."""
    instrument = _make_instrument()
    mock_api = AsyncMock()
    mock_api.request_instrument = AsyncMock(return_value=instrument)

    with patch.object(provider, "_ensure_client", AsyncMock(return_value=mock_api)):
        result = await provider._resolve_instrument("DE000A1A6WE6")

    mock_api.request_instrument.assert_called_once_with(isin="DE000A1A6WE6")
    assert result is instrument


# ---------------------------------------------------------------------------
# _resolve_instrument: ticker path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_instrument_strips_exchange_suffix(provider: OnvistaProvider) -> None:
    """Ticker like AJ91.F is searched by base symbol AJ91."""
    instrument = _make_instrument()
    mock_api = AsyncMock()
    mock_api.search_instrument = AsyncMock(return_value=[instrument])
    mock_api.request_instrument = AsyncMock(return_value=instrument)

    with patch.object(provider, "_ensure_client", AsyncMock(return_value=mock_api)):
        result = await provider._resolve_instrument("AJ91.F")

    mock_api.search_instrument.assert_called_once_with("AJ91", instrument_type="STOCK")
    assert result is instrument


@pytest.mark.asyncio
async def test_resolve_instrument_returns_none_when_no_results(provider: OnvistaProvider) -> None:
    """Returns None when search finds no instruments."""
    mock_api = AsyncMock()
    mock_api.search_instrument = AsyncMock(return_value=[])

    with patch.object(provider, "_ensure_client", AsyncMock(return_value=mock_api)):
        result = await provider._resolve_instrument("INVALID_TICKER")

    assert result is None


# ---------------------------------------------------------------------------
# get_price_history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_price_history_returns_ohlcv_records(provider: OnvistaProvider) -> None:
    """get_price_history maps onvista eod_history parallel arrays to normalized OHLCV dicts."""
    import datetime

    instrument = _make_instrument()

    # eod_history returns parallel arrays: open=first, close=last, plus high/low/volume.
    eod = {
        "datetimeLast": [int(datetime.datetime(2024, 1, 2).timestamp())],
        "first": [12.0],
        "high": [12.8],
        "low": [11.9],
        "last": [12.5],
        "volume": [1000],
    }

    with (
        patch.object(provider, "_resolve_instrument", AsyncMock(return_value=instrument)),
        patch.object(provider, "_fetch_eod_history", AsyncMock(return_value=eod)),
    ):
        result = await provider.get_price_history("AJ91.F", period="1mo", interval="1d")

    assert result is not None
    assert len(result) == 1
    assert result[0]["Open"] == 12.0
    assert result[0]["High"] == 12.8
    assert result[0]["Low"] == 11.9
    assert result[0]["Close"] == 12.5
    assert result[0]["Volume"] == 1000


@pytest.mark.asyncio
async def test_get_price_history_coerces_float_volume_and_skips_bad_rows(provider: OnvistaProvider) -> None:
    """onvista volumes are floats (sometimes fractional); they are coerced to int, and rows
    with malformed OHLC values are skipped rather than aborting the whole series."""
    import datetime

    instrument = _make_instrument()

    eod = {
        "datetimeLast": [
            int(datetime.datetime(2024, 1, 2).timestamp()),
            int(datetime.datetime(2024, 1, 3).timestamp()),
        ],
        "first": [12.0, None],  # second row malformed -> skipped
        "high": [12.8, 13.0],
        "low": [11.9, 12.1],
        "last": [12.5, 12.7],
        "volume": [58336072.43, 1000.0],
    }

    with (
        patch.object(provider, "_resolve_instrument", AsyncMock(return_value=instrument)),
        patch.object(provider, "_fetch_eod_history", AsyncMock(return_value=eod)),
    ):
        result = await provider.get_price_history("AJ91.F", period="1mo", interval="1d")

    assert result is not None
    assert len(result) == 1  # malformed second row dropped
    assert result[0]["Volume"] == 58336072
    assert isinstance(result[0]["Volume"], int)


@pytest.mark.asyncio
async def test_get_price_history_returns_none_for_unsupported_interval(provider: OnvistaProvider) -> None:
    """Unsupported intervals (e.g. 2m) return None so fallback chain continues."""
    result = await provider.get_price_history("AJ91.F", period="1d", interval="2m")
    assert result is None


@pytest.mark.asyncio
async def test_get_price_history_returns_none_when_symbol_not_found(provider: OnvistaProvider) -> None:
    with patch.object(provider, "_resolve_instrument", AsyncMock(return_value=None)):
        result = await provider.get_price_history("UNKNOWN", period="1mo", interval="1d")

    assert result is None


# ---------------------------------------------------------------------------
# get_financials
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_financials_extracts_income_statement(provider: OnvistaProvider) -> None:
    """get_financials extracts statements from onvista's real snapshot field layout.

    Revenue/equity/assets/liabilities/cash come from stocksBalanceSheetList; EBIT/EBITDA/
    operating cash flow from stocksCnFinancialList; both keyed by idYear. Net income is
    derived from onvista's own net margin (cnMarginNet) applied to revenue.
    """
    snapshot = {
        "stocksBalanceSheetList": {
            "list": [
                {
                    "idYear": 2024,
                    "periodeEnd": "2024-12-31",
                    "turnover": 45_000_000,
                    "shareholdersEquity": 20_000_000,
                    "totalAssets": 60_000_000,
                    "liabilities": 40_000_000,
                    "cashReserve": 8_000_000,
                },
                {
                    "idYear": 2023,
                    "periodeEnd": "2023-12-31",
                    "turnover": 41_000_000,
                    "shareholdersEquity": 18_000_000,
                    "totalAssets": 55_000_000,
                    "liabilities": 37_000_000,
                    "cashReserve": 7_000_000,
                },
            ]
        },
        "stocksCnFinancialList": {
            "list": [
                {"idYear": 2024, "cnEbit": 6_200_000, "cnEbitda": 7_500_000, "cnCashflow": 5_000_000, "cnMarginNet": 10.0},
                {"idYear": 2023, "cnEbit": 5_800_000, "cnEbitda": 7_000_000, "cnCashflow": 4_500_000, "cnMarginNet": 10.0},
            ]
        },
    }
    instrument = _make_instrument(snapshot=snapshot)

    with patch.object(provider, "_resolve_instrument", AsyncMock(return_value=instrument)):
        result = await provider.get_financials("AJ91.F", frequency="annual")

    assert result is not None
    assert result["income_statement"]["Total Revenue"]["2024-12-31"] == 45_000_000
    assert result["income_statement"]["EBIT"]["2024-12-31"] == 6_200_000
    assert result["income_statement"]["EBITDA"]["2024-12-31"] == 7_500_000
    # Net income derived from net margin: 41M * 10% = 4.1M
    assert result["income_statement"]["Net Income"]["2023-12-31"] == 4_100_000
    assert result["balance_sheet"]["Stockholders Equity"]["2024-12-31"] == 20_000_000
    assert result["balance_sheet"]["Total Assets"]["2024-12-31"] == 60_000_000
    assert result["balance_sheet"]["Total Liabilities Net Minority Interest"]["2024-12-31"] == 40_000_000
    assert result["balance_sheet"]["Cash And Cash Equivalents"]["2024-12-31"] == 8_000_000
    assert result["cash_flow"]["Operating Cash Flow"]["2024-12-31"] == 5_000_000


@pytest.mark.asyncio
async def test_get_financials_returns_none_for_quarterly(provider: OnvistaProvider) -> None:
    """Quarterly frequency is not supported by onvista; returns None."""
    result = await provider.get_financials("AJ91.F", frequency="quarterly")
    assert result is None


@pytest.mark.asyncio
async def test_get_financials_returns_none_when_symbol_not_found(provider: OnvistaProvider) -> None:
    with patch.object(provider, "_resolve_instrument", AsyncMock(return_value=None)):
        result = await provider.get_financials("UNKNOWN", frequency="annual")

    assert result is None
