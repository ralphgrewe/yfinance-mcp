# Repository Guidelines

## Project Structure & Module Organization
- `src/yfmcp/server.py`: FastMCP server, tool registration. Routes requests through `ProviderRegistry`.
- `src/yfmcp/chart.py`: chart generation (`price_volume`, `vwap`, `volume_profile`) and WebP image encoding.
- `src/yfmcp/types.py`: shared Literal types (`SearchType`, `TopType`, `Period`, `Interval`, `ChartType`, `ErrorCode`, `DataSource`).
- `src/yfmcp/utils.py`: JSON helpers, including `create_error_response()`.
- `src/yfmcp/providers/`: multi-provider data source layer.
  - `base.py`: `BaseProvider` Protocol, `ProviderExhaustedError`.
  - `registry.py`: `ProviderRegistry` — auto-fallback chain and direct source selection.
  - `yfinance_provider.py`: wraps yfinance/yfcache (default provider).
  - `onvista_provider.py`: wraps pyonvista-v2 (UNOFFICIAL_API) — German/EU small caps.
  - `eodhd_provider.py`: wraps EODHD REST API — key-gated via `EODHD_API_KEY` env var.
- `tests/`: async pytest suite for server tools, charts, providers, registry, and type behavior.
- `README.md`: end-user setup and usage guide.

## Architecture Overview
- MCP tools are exposed from `yfmcp.server` with `yfinance_`-prefixed names:
  `yfinance_get_ticker_info`, `yfinance_get_ticker_news`, `yfinance_search`, `yfinance_get_top`, `yfinance_get_price_history`, `yfinance_get_financials`, `yfinance_get_holders`, `yfinance_get_option_chain`, `yfinance_get_option_dates`.
- `yfinance_get_ticker_info`, `yfinance_get_price_history`, `yfinance_get_financials` accept an optional `data_source` parameter (`"auto"` | `"yfinance"` | `"onvista"` | `"eodhd"`). Default is `"auto"` (fallback chain).
- All blocking operations MUST be wrapped with `asyncio.to_thread()` (at the provider layer).
- **Provider contract**: providers return `None` when they have no data (signals registry to try next). Exceptions are caught internally and also yield `None`. Structured error responses are only emitted by `server.py` after all providers are exhausted (`ProviderExhaustedError`).
- Errors MUST be returned via `create_error_response()` with structured JSON (`error`, `error_code`, optional `details`).
- Chart responses are returned as base64-encoded WebP `ImageContent`; tabular history uses JSON arrays.
- Every successful tool response includes a top-level `_provider` field (e.g. `"yfinance"`, `"onvista"`) indicating which source was used.

## Provider Configuration
| Variable         | Default                        | Description                               |
|------------------|--------------------------------|-------------------------------------------|
| `EODHD_API_KEY`  | (unset)                        | If unset, eodhd provider is disabled      |
| `PROVIDER_ORDER` | `yfinance,onvista,eodhd`       | Comma-separated priority order            |
| `ONVISTA_DELAY`  | `0.3`                          | Request delay in seconds for onvista      |

## Build, Test, and Development Commands
- `uv sync`: install runtime dependencies.
- `uv sync --extra dev`: install development dependencies.
- `uv run yfmcp`: run the MCP server.
- `uv run ruff check .` and `uv run ruff format .`: lint/format.
- `uv run ty check src tests`: type check.
- `uv run pytest -v -s --cov=src tests`: full test run with coverage.

## Coding Style & Testing Guidelines
- Python `>=3.12`, line length `120`, one import per line (ruff isort).
- Test files: `tests/test_*.py`; test functions: `test_*`.
- Use `pytest` + `pytest-asyncio` for async behavior.
- If `.pre-commit-config.yaml` exists and code/config changed, run `prek run -a` (fallback: `pre-commit run -a`).

## Commit & Pull Request Guidelines
- Prefer short, imperative commit subjects (for example: `Fix import order`, `Update README`).
- Keep commits focused; include tests when behavior changes.
- PRs should summarize intent and list the validation commands run.

## Configuration & Secrets
- Never commit secrets or generated artifacts.
