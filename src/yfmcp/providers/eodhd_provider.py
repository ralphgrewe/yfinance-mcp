import datetime
from typing import Any

import httpx
from loguru import logger


EODHD_BASE = "https://eodhd.com/api"

# Map yfinance-style period strings to days back
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


def _eodhd_ticker(symbol: str) -> str:
    """
    Convert a yfinance-style ticker to EODHD format.
    Examples: AJ91.F → AJ91.F (XETRA uses .F suffix in EODHD too)
              AAPL → AAPL.US
    If the symbol already contains a dot, return it as-is.
    """
    if "." in symbol:
        return symbol.upper()
    return f"{symbol.upper()}.US"


class EodhdProvider:
    name = "eodhd"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | list | None:
        """Synchronous GET helper (runs in executor via asyncio.to_thread)."""
        url = f"{EODHD_BASE}/{path}"
        query = {"api_token": self._api_key, "fmt": "json"}
        if params:
            query.update(params)
        try:
            response = httpx.get(url, params=query, timeout=30)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            logger.debug("eodhd HTTP {} for {}: {}", exc.response.status_code, url, exc)
            return None
        except Exception as exc:
            logger.debug("eodhd request failed for {}: {}", url, exc)
            return None

    async def get_ticker_info(self, symbol: str) -> dict[str, Any] | None:
        import asyncio

        ticker = _eodhd_ticker(symbol)
        data = await asyncio.to_thread(self._get, f"fundamentals/{ticker}")
        if not data or not isinstance(data, dict):
            return None

        result: dict[str, Any] = {}

        # General section
        general = data.get("General", {})
        if general.get("Ticker"):
            result["symbol"] = general["Ticker"]
        if general.get("ISIN"):
            result["isin"] = general["ISIN"]
        if general.get("Name"):
            result["name"] = general["Name"]
        if general.get("CurrencyCode"):
            result["currency"] = general["CurrencyCode"]
        if general.get("Exchange"):
            result["exchange"] = general["Exchange"]
        if general.get("Sector"):
            result["sector"] = general["Sector"]
        if general.get("Industry"):
            result["industry"] = general["Industry"]
        if general.get("CountryISO"):
            result["country"] = general["CountryISO"]
        if general.get("FullTimeEmployees"):
            try:
                result["employees"] = int(general["FullTimeEmployees"])
            except (ValueError, TypeError):
                pass
        if general.get("WebURL"):
            result["website"] = general["WebURL"]
        if general.get("Description"):
            result["longBusinessSummary"] = general["Description"]

        # Highlights section
        highlights = data.get("Highlights", {})
        if highlights.get("MarketCapitalization"):
            result["marketCap"] = highlights["MarketCapitalization"]
        if highlights.get("PERatio"):
            result["trailingPE"] = highlights["PERatio"]
        if highlights.get("PriceBookMRQ"):
            result["priceToBook"] = highlights["PriceBookMRQ"]
        if highlights.get("EarningsShare"):
            result["trailingEps"] = highlights["EarningsShare"]
        if highlights.get("DividendYield"):
            result["dividendYield"] = highlights["DividendYield"]

        # Valuation section
        valuation = data.get("Valuation", {})
        if valuation.get("EnterpriseValue"):
            result["enterpriseValue"] = valuation["EnterpriseValue"]
        if valuation.get("ForwardPE"):
            result["forwardPE"] = valuation["ForwardPE"]

        # Technicals
        technicals = data.get("Technicals", {})
        if technicals.get("Beta"):
            result["beta"] = technicals["Beta"]
        if technicals.get("52WeekHigh"):
            result["fiftyTwoWeekHigh"] = technicals["52WeekHigh"]
        if technicals.get("52WeekLow"):
            result["fiftyTwoWeekLow"] = technicals["52WeekLow"]

        result["_source_fields"] = [k for k in result if not k.startswith("_")]
        return result if len(result) > 1 else None

    async def get_price_history(
        self,
        symbol: str,
        period: str,
        interval: str,
        prepost: bool = False,
    ) -> list[dict[str, Any]] | None:
        import asyncio

        # EODHD only supports daily (d), weekly (w), monthly (m) via EOD endpoint
        interval_map = {
            "1d": "d",
            "5d": "w",
            "1wk": "w",
            "1mo": "m",
            "3mo": "m",
        }
        eodhd_period = interval_map.get(interval)
        if eodhd_period is None:
            logger.debug("eodhd: unsupported interval '{}', skipping", interval)
            return None

        ticker = _eodhd_ticker(symbol)
        end_dt = datetime.date.today()
        if period == "ytd":
            start_dt = datetime.date(end_dt.year, 1, 1)
        else:
            days = _PERIOD_TO_DAYS.get(period, 365)
            start_dt = end_dt - datetime.timedelta(days=days)

        params = {
            "from": start_dt.isoformat(),
            "to": end_dt.isoformat(),
            "period": eodhd_period,
        }
        data = await asyncio.to_thread(self._get, f"eod/{ticker}", params)
        if not data or not isinstance(data, list):
            return None

        records: list[dict[str, Any]] = []
        for row in data:
            try:
                records.append(
                    {
                        "Date": row["date"],
                        "Open": float(row["open"]),
                        "High": float(row["high"]),
                        "Low": float(row["low"]),
                        "Close": float(row["close"]),
                        "Volume": int(row["volume"]),
                    }
                )
            except (KeyError, ValueError, TypeError) as exc:
                logger.debug("eodhd: skipping malformed row {}: {}", row, exc)
                continue

        return records if records else None

    async def get_financials(
        self,
        symbol: str,
        frequency: str,
    ) -> dict[str, Any] | None:
        import asyncio

        freq_map = {"annual": "yearly", "quarterly": "quarterly"}
        if frequency == "ttm":
            logger.debug("eodhd: ttm frequency not supported, skipping")
            return None

        ticker = _eodhd_ticker(symbol)
        data = await asyncio.to_thread(self._get, f"fundamentals/{ticker}")
        if not data or not isinstance(data, dict):
            return None

        financials = data.get("Financials", {})
        if not financials:
            return None

        result: dict[str, Any] = {}
        freq_key = "annual" if frequency == "annual" else "quarterly"

        # Income statement
        try:
            income_raw = financials.get("Income_Statement", {}).get(freq_key, {})
            if income_raw:
                field_map = {
                    "totalRevenue": "Total Revenue",
                    "netIncome": "Net Income",
                    "ebit": "EBIT",
                    "ebitda": "EBITDA",
                    "operatingIncome": "Operating Income",
                    "interestExpense": "Interest Expense",
                    "incomeTaxExpense": "Tax Provision",
                }
                income: dict[str, Any] = {}
                for _date, row in income_raw.items():
                    if not isinstance(row, dict):
                        continue
                    date_key = row.get("date", _date)
                    for src, dst in field_map.items():
                        val = row.get(src)
                        if val is not None:
                            try:
                                income.setdefault(dst, {})[date_key] = float(val)
                            except (ValueError, TypeError):
                                pass
                if income:
                    result["income_statement"] = income
        except Exception as exc:
            logger.debug("eodhd: income statement extraction failed for {}: {}", symbol, exc)

        # Balance sheet
        try:
            balance_raw = financials.get("Balance_Sheet", {}).get(freq_key, {})
            if balance_raw:
                field_map = {
                    "totalStockholderEquity": "Stockholders Equity",
                    "shortLongTermDebtTotal": "Total Debt",
                    "cashAndCashEquivalentsAtCarryingValue": "Cash And Cash Equivalents",
                    "totalAssets": "Total Assets",
                    "totalLiab": "Total Liabilities Net Minority Interest",
                    "netDebt": "Net Debt",
                }
                balance: dict[str, Any] = {}
                for _date, row in balance_raw.items():
                    if not isinstance(row, dict):
                        continue
                    date_key = row.get("date", _date)
                    for src, dst in field_map.items():
                        val = row.get(src)
                        if val is not None:
                            try:
                                balance.setdefault(dst, {})[date_key] = float(val)
                            except (ValueError, TypeError):
                                pass
                if balance:
                    result["balance_sheet"] = balance
        except Exception as exc:
            logger.debug("eodhd: balance sheet extraction failed for {}: {}", symbol, exc)

        # Cash flow
        try:
            cf_raw = financials.get("Cash_Flow", {}).get(freq_key, {})
            if cf_raw:
                field_map = {
                    "totalCashFromOperatingActivities": "Operating Cash Flow",
                    "freeCashFlow": "Free Cash Flow",
                    "capitalExpenditures": "Capital Expenditure",
                    "depreciation": "Depreciation And Amortization",
                    "dividendsPaid": "Cash Dividends Paid",
                }
                cash_flow: dict[str, Any] = {}
                for _date, row in cf_raw.items():
                    if not isinstance(row, dict):
                        continue
                    date_key = row.get("date", _date)
                    for src, dst in field_map.items():
                        val = row.get(src)
                        if val is not None:
                            try:
                                cash_flow.setdefault(dst, {})[date_key] = float(val)
                            except (ValueError, TypeError):
                                pass
                if cash_flow:
                    result["cash_flow"] = cash_flow
        except Exception as exc:
            logger.debug("eodhd: cash flow extraction failed for {}: {}", symbol, exc)

        return result if result else None

    async def search(self, query: str) -> list[dict[str, Any]] | None:
        import asyncio

        data = await asyncio.to_thread(self._get, "search", {"q": query, "limit": "50"})
        if not data or not isinstance(data, list):
            return None

        results: list[dict[str, Any]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            entry: dict[str, Any] = {}
            if item.get("Code") and item.get("Exchange"):
                entry["symbol"] = f"{item['Code']}.{item['Exchange']}"
            if item.get("ISIN"):
                entry["isin"] = item["ISIN"]
            if item.get("Name"):
                entry["shortname"] = item["Name"]
            if item.get("Type"):
                entry["quoteType"] = item["Type"]
            if item.get("Exchange"):
                entry["exchange"] = item["Exchange"]
            if entry:
                results.append(entry)

        return results if results else None
