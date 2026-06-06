"""Tests for EodhdProvider: API key gating, endpoint construction, field mapping."""

from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from yfmcp.providers.eodhd_provider import EodhdProvider
from yfmcp.providers.eodhd_provider import _eodhd_ticker


# ---------------------------------------------------------------------------
# Ticker format helper
# ---------------------------------------------------------------------------


def test_eodhd_ticker_adds_us_suffix_for_plain_symbol() -> None:
    assert _eodhd_ticker("AAPL") == "AAPL.US"


def test_eodhd_ticker_preserves_existing_suffix() -> None:
    assert _eodhd_ticker("AJ91.F") == "AJ91.F"
    assert _eodhd_ticker("SAP.XETRA") == "SAP.XETRA"


def test_eodhd_ticker_uppercases() -> None:
    assert _eodhd_ticker("aapl") == "AAPL.US"
    assert _eodhd_ticker("aj91.f") == "AJ91.F"


# ---------------------------------------------------------------------------
# get_ticker_info
# ---------------------------------------------------------------------------

_FUNDAMENTALS_RESPONSE = {
    "General": {
        "Ticker": "AJ91",
        "ISIN": "DE000A1A6WE6",
        "Name": "DocCheck AG",
        "CurrencyCode": "EUR",
        "Exchange": "XETRA",
        "Sector": "Healthcare",
        "Industry": "Health Information Services",
        "CountryISO": "DE",
        "FullTimeEmployees": "420",
        "WebURL": "https://doccheck.com",
        "Description": "A health information company.",
    },
    "Highlights": {
        "MarketCapitalization": 87_500_000,
        "PERatio": 18.2,
        "PriceBookMRQ": 2.1,
        "EarningsShare": 0.68,
        "DividendYield": 0.032,
    },
    "Valuation": {
        "EnterpriseValue": 90_000_000,
        "ForwardPE": 16.5,
    },
    "Technicals": {
        "Beta": 0.75,
        "52WeekHigh": 14.0,
        "52WeekLow": 10.5,
    },
    "Financials": {},
}


@pytest.mark.asyncio
async def test_get_ticker_info_maps_fields_correctly() -> None:
    provider = EodhdProvider(api_key="test-key")

    with patch.object(provider, "_get", return_value=_FUNDAMENTALS_RESPONSE):
        result = await provider.get_ticker_info("AJ91.F")

    assert result is not None
    assert result["symbol"] == "AJ91"
    assert result["isin"] == "DE000A1A6WE6"
    assert result["name"] == "DocCheck AG"
    assert result["currency"] == "EUR"
    assert result["exchange"] == "XETRA"
    assert result["sector"] == "Healthcare"
    assert result["industry"] == "Health Information Services"
    assert result["country"] == "DE"
    assert result["employees"] == 420
    assert result["website"] == "https://doccheck.com"
    assert result["marketCap"] == 87_500_000
    assert result["trailingPE"] == 18.2
    assert result["priceToBook"] == 2.1
    assert result["trailingEps"] == 0.68
    assert result["dividendYield"] == 0.032
    assert result["enterpriseValue"] == 90_000_000
    assert result["forwardPE"] == 16.5
    assert result["beta"] == 0.75
    assert result["fiftyTwoWeekHigh"] == 14.0
    assert result["fiftyTwoWeekLow"] == 10.5


@pytest.mark.asyncio
async def test_get_ticker_info_returns_none_when_api_returns_none() -> None:
    provider = EodhdProvider(api_key="test-key")

    with patch.object(provider, "_get", return_value=None):
        result = await provider.get_ticker_info("UNKNOWN")

    assert result is None


@pytest.mark.asyncio
async def test_get_ticker_info_uses_correct_endpoint() -> None:
    """Endpoint constructed as fundamentals/{TICKER}."""
    provider = EodhdProvider(api_key="test-key")
    calls: list[tuple] = []

    def _capture_get(path, params=None):
        calls.append((path, params))
        return None

    with patch.object(provider, "_get", side_effect=_capture_get):
        await provider.get_ticker_info("AJ91.F")

    assert len(calls) == 1
    assert calls[0][0] == "fundamentals/AJ91.F"


# ---------------------------------------------------------------------------
# API key gating (done at registry build-time, not provider level)
# ---------------------------------------------------------------------------


def test_provider_requires_api_key_to_be_instantiated() -> None:
    """EodhdProvider stores the api_key; empty string is technically allowed
    but the registry won't include it when key is unset."""
    p = EodhdProvider(api_key="secret-key")
    assert p._api_key == "secret-key"


def test_get_helper_includes_api_key_in_params() -> None:
    """_get appends api_token to query params."""
    provider = EodhdProvider(api_key="my-key")
    captured: dict = {}

    import httpx

    def _mock_get(url, params, timeout):
        captured.update(params)
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {}
        return mock_resp

    with patch.object(httpx, "get", side_effect=_mock_get):
        provider._get("fundamentals/AAPL.US")

    assert captured.get("api_token") == "my-key"
    assert captured.get("fmt") == "json"


# ---------------------------------------------------------------------------
# get_price_history
# ---------------------------------------------------------------------------

_EOD_RESPONSE = [
    {"date": "2024-01-02", "open": "12.00", "high": "12.80", "low": "11.90", "close": "12.50", "volume": "1000"},
    {"date": "2024-01-03", "open": "12.50", "high": "13.00", "low": "12.40", "close": "12.90", "volume": "1500"},
]


@pytest.mark.asyncio
async def test_get_price_history_returns_ohlcv_records() -> None:
    provider = EodhdProvider(api_key="test-key")

    with patch.object(provider, "_get", return_value=_EOD_RESPONSE):
        result = await provider.get_price_history("AJ91.F", period="1mo", interval="1d")

    assert result is not None
    assert len(result) == 2
    assert result[0] == {"Date": "2024-01-02", "Open": 12.0, "High": 12.8, "Low": 11.9, "Close": 12.5, "Volume": 1000}


@pytest.mark.asyncio
async def test_get_price_history_returns_none_for_unsupported_interval() -> None:
    provider = EodhdProvider(api_key="test-key")
    result = await provider.get_price_history("AAPL.US", period="1d", interval="1m")
    assert result is None


@pytest.mark.asyncio
async def test_get_price_history_returns_none_when_api_returns_none() -> None:
    provider = EodhdProvider(api_key="test-key")

    with patch.object(provider, "_get", return_value=None):
        result = await provider.get_price_history("UNKNOWN", period="1mo", interval="1d")

    assert result is None


@pytest.mark.asyncio
async def test_get_price_history_endpoint_construction() -> None:
    provider = EodhdProvider(api_key="test-key")
    calls: list[tuple] = []

    def _capture(path, params=None):
        calls.append((path, params))
        return None

    with patch.object(provider, "_get", side_effect=_capture):
        await provider.get_price_history("AJ91.F", period="1y", interval="1d")

    assert calls[0][0] == "eod/AJ91.F"
    assert calls[0][1]["period"] == "d"


# ---------------------------------------------------------------------------
# get_financials
# ---------------------------------------------------------------------------

_FINANCIALS_RESPONSE = {
    "General": {},
    "Financials": {
        "Income_Statement": {
            "annual": {
                "2024-12-31": {
                    "date": "2024-12-31",
                    "totalRevenue": "45000000",
                    "netIncome": "4800000",
                    "ebit": "6200000",
                },
                "2023-12-31": {
                    "date": "2023-12-31",
                    "totalRevenue": "41000000",
                    "netIncome": "4200000",
                    "ebit": "5800000",
                },
            }
        },
        "Balance_Sheet": {
            "annual": {
                "2024-12-31": {
                    "date": "2024-12-31",
                    "totalStockholderEquity": "25000000",
                    "totalAssets": "60000000",
                }
            }
        },
        "Cash_Flow": {
            "annual": {
                "2024-12-31": {
                    "date": "2024-12-31",
                    "totalCashFromOperatingActivities": "7000000",
                    "freeCashFlow": "5000000",
                }
            }
        },
    },
}


@pytest.mark.asyncio
async def test_get_financials_maps_income_statement() -> None:
    provider = EodhdProvider(api_key="test-key")

    with patch.object(provider, "_get", return_value=_FINANCIALS_RESPONSE):
        result = await provider.get_financials("AJ91.F", frequency="annual")

    assert result is not None
    assert "income_statement" in result
    assert result["income_statement"]["Total Revenue"]["2024-12-31"] == 45_000_000
    assert result["income_statement"]["Net Income"]["2023-12-31"] == 4_200_000
    assert result["income_statement"]["EBIT"]["2024-12-31"] == 6_200_000


@pytest.mark.asyncio
async def test_get_financials_maps_balance_sheet() -> None:
    provider = EodhdProvider(api_key="test-key")

    with patch.object(provider, "_get", return_value=_FINANCIALS_RESPONSE):
        result = await provider.get_financials("AJ91.F", frequency="annual")

    assert result is not None
    assert "balance_sheet" in result
    assert result["balance_sheet"]["Stockholders Equity"]["2024-12-31"] == 25_000_000


@pytest.mark.asyncio
async def test_get_financials_maps_cash_flow() -> None:
    provider = EodhdProvider(api_key="test-key")

    with patch.object(provider, "_get", return_value=_FINANCIALS_RESPONSE):
        result = await provider.get_financials("AJ91.F", frequency="annual")

    assert result is not None
    assert "cash_flow" in result
    assert result["cash_flow"]["Operating Cash Flow"]["2024-12-31"] == 7_000_000
    assert result["cash_flow"]["Free Cash Flow"]["2024-12-31"] == 5_000_000


@pytest.mark.asyncio
async def test_get_financials_returns_none_for_ttm() -> None:
    provider = EodhdProvider(api_key="test-key")
    result = await provider.get_financials("AAPL.US", frequency="ttm")
    assert result is None


@pytest.mark.asyncio
async def test_get_financials_returns_none_when_no_financials_section() -> None:
    provider = EodhdProvider(api_key="test-key")

    with patch.object(provider, "_get", return_value={"General": {}}):
        result = await provider.get_financials("AAPL.US", frequency="annual")

    assert result is None
