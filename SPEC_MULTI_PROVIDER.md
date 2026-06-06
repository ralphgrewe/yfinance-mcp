# Specification: Multi-Provider Data Source Architecture

## Overview

Extend the yfinance MCP server to support multiple financial data sources beyond yfinance,
with a focus on European and German small-cap stocks (e.g. DOCCHECK / AJ91.F) that are
poorly covered by Yahoo Finance.

---

## Design Decision: Automatic Fallback + Optional Override

**Both** automatic fallback and explicit user selection are implemented:

1. **Automatic fallback chain** (default): Try providers in priority order; return the first
   successful result. The MCP tool caller (Claude) does not need to know about data sources.

2. **Explicit `data_source` parameter** (optional override): Any tool that fetches
   single-instrument data accepts an optional `data_source` parameter. When supplied, skip
   the fallback logic and query that source directly. Useful when the caller wants a specific
   source for reproducibility or comparison.

**Why both?** Automated pipelines (skills, scheduled tasks) benefit from transparent fallback
without any parameter changes. Power users and Claude's analyst skills may want to pin a
source (e.g. always use onvista for EU stocks, always use EODHD for fundamentals).

---

## Provider Registry

Define a provider enum and priority order in `src/yfmcp/providers.py`:

```python
DataSource = Literal["auto", "yfinance", "onvista", "eodhd"]
```

**Priority order for `auto`:**

| Priority | Provider  | Best for                                        |
|----------|-----------|-------------------------------------------------|
| 1        | yfinance  | US stocks, major EU blue chips, broad coverage  |
| 2        | onvista   | German/EU small caps, XETRA-listed stocks       |
| 3        | eodhd     | Fallback fundamentals; requires API key config  |

---

## File / Module Structure

```
src/yfmcp/
  server.py              # existing — add data_source param to tools
  types.py               # existing — add DataSource literal
  utils.py               # existing — unchanged
  chart.py               # existing — unchanged
  providers/
    __init__.py          # exports ProviderRegistry, DataSource
    base.py              # abstract BaseProvider protocol
    yfinance_provider.py # wraps existing yfinance/yfcache logic (extracted from server.py)
    onvista_provider.py  # wraps pyonvista-v2
    eodhd_provider.py    # wraps eodhd REST API (optional, key-gated)
    registry.py          # ProviderRegistry: auto-fallback + direct selection
```

---

## Abstract Provider Protocol

File: `src/yfmcp/providers/base.py`

```python
from typing import Protocol, Any

class BaseProvider(Protocol):
    name: str  # "yfinance" | "onvista" | "eodhd"

    async def get_ticker_info(self, symbol: str) -> dict[str, Any] | None:
        """Returns normalized ticker info dict or None if unavailable."""
        ...

    async def get_price_history(
        self, symbol: str, period: str, interval: str
    ) -> list[dict[str, Any]] | None:
        """Returns list of OHLCV dicts or None if unavailable."""
        ...

    async def get_financials(
        self, symbol: str, frequency: str
    ) -> dict[str, Any] | None:
        """Returns income/balance/cashflow dict or None if unavailable."""
        ...

    async def search(self, query: str) -> list[dict[str, Any]] | None:
        """Returns list of matching instrument dicts or None."""
        ...
```

Providers return `None` (not an error response) when they have no data for a symbol —
this signals the registry to try the next provider. Exceptions are caught internally
and also yield `None`. Structured error responses are only emitted at the `server.py`
layer after all providers are exhausted.

---

## ProviderRegistry

File: `src/yfmcp/providers/registry.py`

```python
class ProviderRegistry:
    def __init__(self, providers: list[BaseProvider]):
        # providers are ordered by priority
        self._providers = providers
        self._by_name = {p.name: p for p in providers}

    async def get(
        self,
        method: str,          # e.g. "get_ticker_info"
        source: DataSource,   # "auto" | specific name
        **kwargs,
    ) -> tuple[Any, str]:     # (result, provider_name_used)
        """
        Returns (result, provider_name) or raises ProviderExhaustedError.
        When source == "auto", iterates providers in priority order.
        When source is a specific name, queries only that provider.
        """
        ...
```

The `server.py` tools receive `(result, source_used)` and include `_provider` in the
JSON response metadata so callers know which source was used.

---

## Normalized Data Schema

All providers MUST map their output to the shared normalized schema below.
Fields absent in a provider's data are omitted (not set to null).

### Ticker Info (common fields)

```json
{
  "symbol": "AJ91.F",
  "isin": "DE000A1A6WE6",
  "name": "DocCheck AG",
  "currency": "EUR",
  "exchange": "XETRA",
  "sector": "Healthcare",
  "industry": "Health Information Services",
  "country": "DE",
  "employees": 420,
  "website": "https://doccheck.com",
  "currentPrice": 12.50,
  "marketCap": 87500000,
  "trailingPE": 18.2,
  "dividendYield": 0.032,
  "_provider": "onvista",
  "_source_fields": ["pe_ratio", "market_cap", "dividend_yield"]
}
```

`_provider` and `_source_fields` are metadata injected by the registry layer.

### Financials (common fields)

```json
{
  "income_statement": {
    "Total Revenue": {"2024-12-31": 45000000, "2023-12-31": 41000000},
    "Net Income": {"2024-12-31": 4800000, "2023-12-31": 4200000},
    "EBIT": {"2024-12-31": 6200000}
  },
  "balance_sheet": { ... },
  "cash_flow": { ... },
  "_provider": "onvista"
}
```

---

## Provider Implementation Notes

### yfinance_provider.py

Extract the existing logic from `server.py` into this provider class.
No behavior change — this is a refactor, not a rewrite.
The server.py tools become thin wrappers that call `registry.get(...)`.

### onvista_provider.py

Dependency: `pyonvista-v2` (MIT, `pip install pyonvista-v2`).
Add to `pyproject.toml` dependencies.

Key mapping from onvista fields to normalized schema:

| onvista field         | normalized field    |
|-----------------------|---------------------|
| `ratios.pe_ratio`     | `trailingPE`        |
| `ratios.pb_ratio`     | `priceToBook`       |
| `ratios.eps`          | `trailingEps`       |
| `ratios.dividend_yield` | `dividendYield`   |
| `ratios.market_cap`   | `marketCap`         |
| `company.sector`      | `sector`            |
| `company.employees`   | `employees`         |
| `quote.close`         | `currentPrice`      |

Symbol resolution: onvista works best with ISIN. The provider should:
1. Accept standard ticker (e.g. `AJ91.F`) or ISIN.
2. If ticker given, attempt direct onvista search; use first result matching the ticker/ISIN.

**Rate limiting:** use `PyOnVista(request_delay=0.3)`.

**ToS caveat:** onvista has no public API; this library uses their internal REST endpoints.
Mark as `# UNOFFICIAL_API` in the code for visibility. Suitable for personal/research use.

### eodhd_provider.py

Dependency: `requests` or `httpx` (already likely present).
Requires environment variable `EODHD_API_KEY`.

If key is not set, `eodhd_provider` is excluded from the registry at startup (log a warning).

Base URL: `https://eodhd.com/api/`

Relevant endpoints:
- `GET /fundamentals/{TICKER}.{EXCHANGE}?api_token=...&fmt=json` — full fundamentals
- `GET /eod/{TICKER}.{EXCHANGE}?api_token=...&fmt=json&period=d` — price history

EODHD ticker format for XETRA: `AJ91.XETRA` or `AJ91.F`.

---

## Changes to server.py

### New `data_source` parameter

Add to the following tools:
- `yfinance_get_ticker_info`
- `yfinance_get_price_history`
- `yfinance_get_financials`

Parameter definition (add to each tool signature):

```python
data_source: Annotated[
    DataSource,
    Field(
        description=(
            "Data provider to use. 'auto' (default) tries providers in order: "
            "yfinance → onvista → eodhd. "
            "Use 'onvista' for German/EU small caps not covered by yfinance. "
            "Use 'eodhd' if an API key is configured (see EODHD_API_KEY env var)."
        )
    ),
] = "auto"
```

Do NOT add `data_source` to: `yfinance_get_option_chain`, `yfinance_get_option_dates`,
`yfinance_get_holders`, `yfinance_get_top` — these are inherently US/Yahoo-Finance-specific
or sector-level tools with no onvista equivalent.

### Response metadata

Every tool response JSON includes a top-level `_provider` field indicating which source
was used. Example:

```json
{
  "symbol": "AJ91.F",
  "currentPrice": 12.50,
  "_provider": "onvista"
}
```

---

## Configuration

Provider availability and ordering are controlled by environment variables:

| Variable            | Default  | Description                                    |
|---------------------|----------|------------------------------------------------|
| `EODHD_API_KEY`     | (unset)  | If unset, eodhd provider is disabled           |
| `PROVIDER_ORDER`    | `yfinance,onvista,eodhd` | Comma-separated priority order  |
| `ONVISTA_DELAY`     | `0.3`    | Request delay in seconds for onvista           |

---

## Error Handling

- Provider returns `None` → registry tries next provider.
- All providers return `None` → `server.py` emits `create_error_response` with
  `error_code="NO_DATA"` and `details.providers_tried: ["yfinance", "onvista"]`.
- `data_source` is set to a specific provider that returns `None` → error response
  indicates which provider was used, does NOT fall back.

---

## New pyproject.toml Dependencies

```toml
[project.dependencies]
# existing...
pyonvista-v2 = ">=2.0.2"
httpx = ">=0.27"          # for eodhd HTTP calls (if not already present)
```

---

## Testing

New test files:
- `tests/test_onvista_provider.py` — mock `aiohttp.ClientSession`; test ISIN lookup, field mapping, None on missing symbol
- `tests/test_eodhd_provider.py` — mock `httpx`; test key-gating, endpoint construction, field mapping
- `tests/test_registry.py` — test fallback chain (provider1 returns None → provider2 used), direct source selection, exhausted error

Extend existing tests:
- `tests/test_server.py` — add `data_source` parameter permutations to existing tool tests

---

## Implementation Order

1. Create `src/yfmcp/providers/base.py` — protocol + `ProviderExhaustedError`
2. Create `src/yfmcp/providers/yfinance_provider.py` — extract from `server.py`
3. Create `src/yfmcp/providers/registry.py`
4. Refactor `server.py` to use registry (behavior-identical, all existing tests must pass)
5. Create `src/yfmcp/providers/onvista_provider.py`
6. Add `DataSource` to `types.py`; add `data_source` param to relevant tools in `server.py`
7. Create `src/yfmcp/providers/eodhd_provider.py` (key-gated)
8. Write tests
9. Update `AGENTS.md` and `README.md`
