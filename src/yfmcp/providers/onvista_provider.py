# UNOFFICIAL_API: onvista has no public API; pyonvista-v2 uses their internal REST endpoints.
# Suitable for personal/research use only.
import asyncio
import datetime
from collections.abc import Awaitable
from typing import Any

import aiohttp
from loguru import logger
from pyonvista import PyOnVista


# onvista's API returns HTTP 429 to aiohttp's default User-Agent. The pyonvista library
# retries 429 via *unbounded recursion* (1s sleep each), so a blocked UA hangs for ~17
# minutes before crashing. Sending a browser-like UA avoids the 429 entirely.
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


_ONVISTA_API_BASE = "https://api.onvista.de/api/v1"

# Map yfinance-style period strings to timedelta
_PERIOD_TO_DAYS: dict[str, int] = {
    "1d": 1,
    "5d": 5,
    "1mo": 31,
    "3mo": 92,
    "6mo": 183,
    "1y": 365,
    "2y": 730,
    "5y": 1825,
    "10y": 3650,
    "ytd": -1,  # handled specially
    "max": 3650 * 3,
}

# Intraday intervals are not available via the eod_history endpoint (daily EOD only).
_INTRADAY_INTERVALS = {"1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h"}


class OnvistaProvider:
    name = "onvista"

    def __init__(self, request_delay: float = 0.3, timeout: int = 30) -> None:
        self._request_delay = request_delay
        self._timeout = timeout
        self._client: aiohttp.ClientSession | None = None
        self._api: PyOnVista | None = None
        self._lock = asyncio.Lock()

    async def _ensure_client(self) -> PyOnVista:
        """Lazily create the aiohttp session and PyOnVista client."""
        async with self._lock:
            if self._api is None or self._client is None or self._client.closed:
                if self._client and not self._client.closed:
                    await self._client.close()
                # Browser User-Agent avoids onvista's HTTP 429 for default aiohttp UA.
                # Explicit session timeout guards each underlying HTTP call.
                session_timeout = aiohttp.ClientTimeout(total=self._timeout)
                self._client = aiohttp.ClientSession(
                    timeout=session_timeout,
                    headers={"User-Agent": _BROWSER_USER_AGENT},
                )
                self._api = PyOnVista(request_delay=self._request_delay, timeout=self._timeout)
                await self._api.install_client(self._client)
        return self._api

    async def _call(self, coro: Awaitable[Any]) -> Any:
        """Run a single pyonvista API call with a hard overall timeout.

        Defense-in-depth: even if onvista rate-limits (HTTP 429), the library retries via
        unbounded recursion (~1000 deep, ~17 min) before failing. wait_for caps any single
        call so a hang can never block the MCP server; callers treat TimeoutError as 'no data'.
        """
        return await asyncio.wait_for(coro, timeout=self._timeout + 5)

    @staticmethod
    def _looks_like_wkn(symbol: str) -> bool:
        """WKNs are exactly 6 uppercase alphanumeric characters with no exchange suffix."""
        return "." not in symbol and len(symbol) == 6 and symbol.isalnum()

    async def _resolve_instrument(self, symbol: str) -> Any | None:
        """Resolve a ticker symbol, WKN, or ISIN to an onvista Instrument with snapshot data."""
        api = await self._ensure_client()

        # If it looks like an ISIN (12 alphanumeric chars starting with 2 letters), use direct lookup
        if len(symbol) == 12 and symbol[:2].isalpha() and symbol[2:].isalnum():
            try:
                instrument = await self._call(api.request_instrument(isin=symbol.upper()))
                return instrument
            except Exception as exc:
                logger.debug("onvista ISIN lookup failed for {}: {}", symbol, exc)
                return None

        # Strip exchange suffix (e.g. AJ91.F → AJ91, SAP.DE → SAP)
        base_symbol = symbol.split(".")[0].upper()

        # WKNs don't match instrument symbols, so search without type filter and use first result
        if self._looks_like_wkn(base_symbol):
            try:
                results = await self._call(api.search_instrument(base_symbol))
            except Exception as exc:
                logger.debug("onvista WKN search failed for {}: {}", base_symbol, exc)
                return None
            if not results:
                logger.debug("onvista: no results for WKN {}", base_symbol)
                return None
            try:
                instrument = await self._call(api.request_instrument(instrument=results[0]))
                return instrument
            except Exception as exc:
                logger.debug("onvista request_instrument failed for WKN {}: {}", base_symbol, exc)
                return None

        # Try direct search by base symbol, preferring stocks
        try:
            results = await self._call(api.search_instrument(base_symbol, instrument_type="STOCK"))
        except Exception as exc:
            logger.debug("onvista search_instrument failed for {}: {}", base_symbol, exc)
            return None

        if not results:
            logger.debug("onvista: no results for symbol {}", symbol)
            return None

        # Prefer exact symbol match, then partial match
        exact = [r for r in results if r.symbol and r.symbol.upper() == base_symbol]
        candidates = exact or results

        # Fetch full snapshot for the first candidate
        try:
            instrument = await self._call(api.request_instrument(instrument=candidates[0]))
            return instrument
        except Exception as exc:
            logger.debug("onvista request_instrument failed for {}: {}", symbol, exc)
            return None

    async def get_ticker_info(self, symbol: str) -> dict[str, Any] | None:
        try:
            instrument = await self._resolve_instrument(symbol)
        except Exception as exc:
            logger.debug("onvista get_ticker_info failed for {}: {}", symbol, exc)
            return None

        if instrument is None:
            return None

        result: dict[str, Any] = {}

        # Basic instrument fields
        if instrument.symbol:
            result["symbol"] = instrument.symbol
        if instrument.isin:
            result["isin"] = instrument.isin
        if instrument.name:
            result["name"] = instrument.name

        # Current price from latest quote
        if instrument.quote is not None:
            result["currentPrice"] = instrument.quote.close
            result["currency"] = instrument.notations[0].currency if instrument.notations else None

        # Financial ratios
        try:
            ratios = instrument.get_financial_ratios()
            if ratios.pe_ratio is not None:
                result["trailingPE"] = ratios.pe_ratio
            if ratios.pb_ratio is not None:
                result["priceToBook"] = ratios.pb_ratio
            if ratios.eps is not None:
                result["trailingEps"] = ratios.eps
            if ratios.dividend_yield is not None:
                result["dividendYield"] = ratios.dividend_yield
            if ratios.market_cap is not None:
                result["marketCap"] = ratios.market_cap
        except Exception as exc:
            logger.debug("onvista: failed to extract financial ratios for {}: {}", symbol, exc)

        # Company info
        try:
            company = instrument.get_company_info()
            if company.sector:
                result["sector"] = company.sector
            if company.industry:
                result["industry"] = company.industry
            if company.country:
                result["country"] = company.country
            if company.employees:
                result["employees"] = company.employees
            if company.website:
                result["website"] = company.website
            if company.headquarters:
                result["exchange"] = company.headquarters
        except Exception as exc:
            logger.debug("onvista: failed to extract company info for {}: {}", symbol, exc)

        # Track which normalized fields came from this provider
        result["_source_fields"] = [k for k in result if not k.startswith("_")]

        return result if len(result) > 1 else None

    async def _fetch_eod_history(self, instrument: Any, start_date: str) -> dict[str, Any] | None:
        """Fetch daily EOD price history from onvista's eod_history endpoint.

        pyonvista's request_quotes targets the legacy chart_history endpoint, which now
        returns HTTP 403. The eod_history endpoint returns the same parallel-array payload
        and is still accessible with a browser User-Agent.

        The endpoint requires a 'range' code; ranges >= M1 cap the row count, but combining
        'range=MAX' with an explicit 'startDate' returns the full daily series from that date
        to today, so we always use range=MAX and control the window via startDate.
        """
        await self._ensure_client()
        if not instrument.notations:
            logger.debug("onvista: instrument {} has no notations for eod_history", instrument.symbol)
            return None
        notation = instrument.notations[0]
        url = (
            f"{_ONVISTA_API_BASE}/instruments/{instrument.type}/{instrument.uid}/eod_history"
            f"?idNotation={notation.id}&range=MAX&startDate={start_date}"
        )

        async def _do() -> dict[str, Any] | None:
            assert self._client is not None
            async with self._client.get(url) as resp:
                if resp.status != 200:
                    logger.debug("onvista eod_history HTTP {} for {}", resp.status, url)
                    return None
                return dict(await resp.json())

        return await self._call(_do())

    async def get_price_history(
        self,
        symbol: str,
        period: str,
        interval: str,
        prepost: bool = False,
    ) -> list[dict[str, Any]] | None:
        # eod_history provides daily end-of-day data only; intraday is not available here.
        if interval in _INTRADAY_INTERVALS:
            logger.debug("onvista: intraday interval '{}' unsupported, skipping", interval)
            return None

        # Compute the window start date; eod_history clamps to the earliest available history.
        now = datetime.datetime.now()
        if period == "ytd":
            start_dt = datetime.datetime(now.year, 1, 1)
        elif period == "max":
            start_dt = datetime.datetime(2000, 1, 1)
        else:
            start_dt = now - datetime.timedelta(days=_PERIOD_TO_DAYS.get(period, 365))
        start_date = start_dt.strftime("%Y-%m-%d")

        try:
            instrument = await self._resolve_instrument(symbol)
        except Exception as exc:
            logger.debug("onvista get_price_history resolve failed for {}: {}", symbol, exc)
            return None

        if instrument is None:
            return None

        try:
            data = await self._fetch_eod_history(instrument, start_date)
        except Exception as exc:
            logger.debug("onvista eod_history failed for {}: {}", symbol, exc)
            return None

        if not data or not data.get("datetimeLast"):
            return None

        timestamps = data.get("datetimeLast", [])
        opens = data.get("first", [])
        closes = data.get("last", [])
        highs = data.get("high", [])
        lows = data.get("low", [])
        volumes = data.get("volume", [])

        records: list[dict[str, Any]] = []
        for i, ts in enumerate(timestamps):
            try:
                # onvista reports volume as a float (occasionally fractional for ADRs); coerce
                # to int to match the yfinance/eodhd providers' OHLCV schema.
                volume = volumes[i] if i < len(volumes) else None
                records.append(
                    {
                        "Date": datetime.datetime.fromtimestamp(ts).isoformat(),
                        "Open": float(opens[i]),
                        "High": float(highs[i]),
                        "Low": float(lows[i]),
                        "Close": float(closes[i]),
                        "Volume": int(volume) if volume is not None else None,
                    }
                )
            except (IndexError, ValueError, TypeError) as exc:
                logger.debug("onvista: skipping malformed eod row {}: {}", i, exc)
                continue

        return records if records else None

    async def get_financials(
        self,
        symbol: str,
        frequency: str,
    ) -> dict[str, Any] | None:
        # onvista snapshot only provides annual aggregates, not quarterly/TTM breakdown
        if frequency not in {"annual"}:
            logger.debug("onvista: unsupported frequency '{}', skipping", frequency)
            return None

        try:
            instrument = await self._resolve_instrument(symbol)
        except Exception as exc:
            logger.debug("onvista get_financials resolve failed for {}: {}", symbol, exc)
            return None

        if instrument is None:
            return None

        snapshot = instrument._snapshot_json
        if not snapshot:
            return None

        result: dict[str, Any] = {}

        # onvista exposes per-year fundamentals across two parallel lists, keyed by year:
        #   - stocksBalanceSheetList: revenue (turnover), equity, assets, liabilities, cash, ...
        #   - stocksCnFinancialList:  EBIT, EBITDA, operating cash flow, net margin, ...
        balance_by_year = self._index_by_year(snapshot.get("stocksBalanceSheetList", {}).get("list", []))
        financial_by_year = self._index_by_year(snapshot.get("stocksCnFinancialList", {}).get("list", []))

        income: dict[str, dict[str, float]] = {}
        balance: dict[str, dict[str, float]] = {}
        cash_flow: dict[str, dict[str, float]] = {}

        for year, entry in balance_by_year.items():
            date_key = self._year_date_key(entry, year)
            turnover = self._to_float(entry.get("turnover"))
            equity = self._to_float(entry.get("shareholdersEquity")) or self._to_float(entry.get("equityCapital"))
            total_assets = self._to_float(entry.get("totalAssets"))
            total_liabilities = self._to_float(entry.get("liabilities")) or self._to_float(entry.get("foreignCapital"))
            cash = self._to_float(entry.get("cashReserve"))
            fin = financial_by_year.get(year, {})
            net_margin = self._to_float(fin.get("cnMarginNet"))

            if turnover is not None:
                income.setdefault("Total Revenue", {})[date_key] = turnover
                # Net income is not provided directly; derive from onvista's own net margin (%).
                if net_margin is not None:
                    income.setdefault("Net Income", {})[date_key] = round(turnover * net_margin / 100.0, 2)
            if equity is not None:
                balance.setdefault("Stockholders Equity", {})[date_key] = equity
            if total_assets is not None:
                balance.setdefault("Total Assets", {})[date_key] = total_assets
            if total_liabilities is not None:
                balance.setdefault("Total Liabilities Net Minority Interest", {})[date_key] = total_liabilities
            if cash is not None:
                balance.setdefault("Cash And Cash Equivalents", {})[date_key] = cash

        for year, entry in financial_by_year.items():
            date_key = self._year_date_key(entry, year)
            ebit = self._to_float(entry.get("cnEbit"))
            ebitda = self._to_float(entry.get("cnEbitda"))
            op_cash_flow = self._to_float(entry.get("cnCashflow"))
            if ebit is not None:
                income.setdefault("EBIT", {})[date_key] = ebit
            if ebitda is not None:
                income.setdefault("EBITDA", {})[date_key] = ebitda
            if op_cash_flow is not None:
                cash_flow.setdefault("Operating Cash Flow", {})[date_key] = op_cash_flow

        if income:
            result["income_statement"] = income
        if balance:
            result["balance_sheet"] = balance
        if cash_flow:
            result["cash_flow"] = cash_flow

        return result if result else None

    @staticmethod
    def _index_by_year(entries: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
        """Index onvista financial list entries by their integer year (idYear / label)."""
        by_year: dict[int, dict[str, Any]] = {}
        for entry in entries:
            year = entry.get("idYear")
            if year is None:
                label = entry.get("label")
                try:
                    year = int(label) if label is not None else None
                except (ValueError, TypeError):
                    year = None
            if year is not None:
                by_year[int(year)] = entry
        return by_year

    @staticmethod
    def _year_date_key(entry: dict[str, Any], year: int) -> str:
        """Prefer the entry's explicit period end date, else fall back to Dec 31 of the year."""
        period_end = entry.get("periodeEnd")
        if isinstance(period_end, str) and len(period_end) >= 10:
            return period_end[:10]
        return f"{year}-12-31"

    @staticmethod
    def _to_float(value: Any) -> float | None:
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None

    async def search(self, query: str) -> list[dict[str, Any]] | None:
        try:
            api = await self._ensure_client()
            instruments = await self._call(api.search_instrument(query))
        except Exception as exc:
            logger.debug("onvista search failed for {}: {}", query, exc)
            return None

        if not instruments:
            return None

        results = []
        for inst in instruments:
            entry: dict[str, Any] = {}
            if inst.symbol:
                entry["symbol"] = inst.symbol
            if inst.isin:
                entry["isin"] = inst.isin
            if inst.name:
                entry["shortname"] = inst.name
            if inst.type:
                entry["quoteType"] = inst.type
            if entry:
                results.append(entry)

        return results if results else None
