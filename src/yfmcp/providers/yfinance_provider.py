import asyncio
from typing import Any

import yfinance as yf
import yfinance_cache as yfcache
from loguru import logger
from yfinance.exceptions import YFRateLimitError


_RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
    ConnectionError,
    TimeoutError,
    OSError,
    YFRateLimitError,
)


class YFinanceProvider:
    name = "yfinance"

    async def get_ticker_info(self, symbol: str) -> dict[str, Any] | None:
        # Try yfcache first; fall back to plain yf.Ticker on AttributeError (yfinance-cache
        # compatibility breaks with yfinance>=1.3 for some symbols, e.g. '_dividends' missing).
        for ticker_factory in [yfcache.Ticker, yf.Ticker]:
            try:
                ticker = await asyncio.to_thread(ticker_factory, symbol)
                info = await asyncio.to_thread(lambda: ticker.info)
                if info:
                    return dict(info)
            except AttributeError as exc:
                logger.debug("yfinance get_ticker_info yfcache AttributeError for {}, retrying with yf: {}", symbol, exc)
                continue
            except Exception as exc:
                logger.debug("yfinance get_ticker_info failed for {}: {}", symbol, exc)
                return None
        return None

    async def get_price_history(
        self,
        symbol: str,
        period: str,
        interval: str,
        prepost: bool = False,
    ) -> list[dict[str, Any]] | None:
        try:
            if prepost:
                ticker = await asyncio.to_thread(yf.Ticker, symbol)
                df = await asyncio.to_thread(
                    ticker.history,
                    period=period,
                    interval=interval,
                    prepost=prepost,
                    rounding=True,
                )
            else:
                ticker = await asyncio.to_thread(yfcache.Ticker, symbol)
                df = await asyncio.to_thread(
                    ticker.history,
                    period=period,
                    interval=interval,
                    rounding=True,
                )
        except Exception as exc:
            logger.debug("yfinance get_price_history failed for {}: {}", symbol, exc)
            return None

        if df is None or df.empty:
            return None

        return df.reset_index().to_dict(orient="records")  # type: ignore[return-value]

    async def get_financials(
        self,
        symbol: str,
        frequency: str,
        _build_response_fn: Any = None,
    ) -> dict[str, Any] | None:
        try:
            if frequency == "ttm":
                ticker = await asyncio.to_thread(yf.Ticker, symbol)
            else:
                ticker = await asyncio.to_thread(yfcache.Ticker, symbol)
        except Exception as exc:
            logger.debug("yfinance get_financials (ticker init) failed for {}: {}", symbol, exc)
            return None

        try:
            if frequency == "annual":
                income_stmt = await asyncio.to_thread(lambda: ticker.income_stmt)
                balance_sheet = await asyncio.to_thread(lambda: ticker.balance_sheet)
                cash_flow = await asyncio.to_thread(lambda: ticker.cashflow)
            elif frequency == "quarterly":
                income_stmt = await asyncio.to_thread(lambda: ticker.quarterly_income_stmt)
                balance_sheet = await asyncio.to_thread(lambda: ticker.quarterly_balance_sheet)
                cash_flow = await asyncio.to_thread(lambda: ticker.quarterly_cashflow)
            elif frequency == "ttm":
                income_stmt = await asyncio.to_thread(lambda: ticker.ttm_income_stmt)
                balance_sheet = None
                cash_flow = None
            else:
                return None
        except Exception as exc:
            logger.debug("yfinance get_financials (data fetch) failed for {}: {}", symbol, exc)
            return None

        if _build_response_fn is not None:
            result = _build_response_fn(income_stmt, balance_sheet, cash_flow)
        else:
            result = _default_build_financials(income_stmt, balance_sheet, cash_flow)

        return result if result else None

    async def search(self, query: str) -> list[dict[str, Any]] | None:
        try:
            s = await asyncio.to_thread(yf.Search, query)
            quotes = s.quotes
        except Exception as exc:
            logger.debug("yfinance search failed for {}: {}", query, exc)
            return None

        if not quotes:
            return None
        return list(quotes)  # type: ignore[return-value]


def _default_build_financials(income_stmt: Any, balance_sheet: Any, cash_flow: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}

    if income_stmt is not None and not income_stmt.empty:
        income_fields = [
            "EBIT",
            "Net Income",
            "Tax Provision",
            "Pretax Income",
            "Interest Expense",
            "Total Revenue",
            "Operating Income",
            "EBITDA",
            "Normalized Income",
        ]
        available = [f for f in income_fields if f in income_stmt.index]
        result["income_statement"] = {
            field: {str(col.date()): income_stmt.loc[field, col] for col in income_stmt.columns}
            for field in available
        }

    if balance_sheet is not None and not balance_sheet.empty:
        balance_fields = [
            "Stockholders Equity",
            "Total Debt",
            "Cash And Cash Equivalents",
            "Invested Capital",
            "Net Debt",
            "Total Assets",
            "Total Liabilities Net Minority Interest",
            "Net Tangible Assets",
            "Tangible Book Value",
        ]
        available = [f for f in balance_fields if f in balance_sheet.index]
        result["balance_sheet"] = {
            field: {str(col.date()): balance_sheet.loc[field, col] for col in balance_sheet.columns}
            for field in available
        }

    if cash_flow is not None and not cash_flow.empty:
        cash_flow_fields = [
            "Operating Cash Flow",
            "Free Cash Flow",
            "Capital Expenditure",
            "Net Income From Continuing Operations",
            "Depreciation And Amortization",
            "Change In Working Capital",
            "Cash Dividends Paid",
        ]
        available = [f for f in cash_flow_fields if f in cash_flow.index]
        result["cash_flow"] = {
            field: {str(col.date()): cash_flow.loc[field, col] for col in cash_flow.columns}
            for field in available
        }

    return result
