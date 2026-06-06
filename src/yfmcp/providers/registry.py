from typing import Any

from loguru import logger

from yfmcp.providers.base import BaseProvider
from yfmcp.providers.base import ProviderExhaustedError
from yfmcp.types import DataSource


def _looks_like_wkn(symbol: str) -> bool:
    """WKNs are exactly 6 uppercase alphanumeric characters with no exchange suffix."""
    if "." in symbol or len(symbol) != 6:
        return False
    return symbol.isalnum()


class ProviderRegistry:
    def __init__(self, providers: list[BaseProvider]) -> None:
        # providers are ordered by priority
        self._providers = providers
        self._by_name = {p.name: p for p in providers}

    async def get(
        self,
        method: str,
        source: DataSource,
        **kwargs: Any,
    ) -> tuple[Any, str]:
        """Returns (result, provider_name_used) or raises ProviderExhaustedError.

        When source == "auto", iterates providers in priority order.
        When source == "auto" and the symbol looks like a WKN (6 alphanumeric chars),
        onvista is tried first since WKNs are German/EU identifiers.
        When source is a specific name, queries only that provider.
        """
        if source == "auto":
            symbol = kwargs.get("symbol", "")
            if isinstance(symbol, str) and _looks_like_wkn(symbol.upper()):
                logger.debug("Symbol '{}' looks like a WKN — routing onvista first", symbol)
                onvista = self._by_name.get("onvista")
                others = [p for p in self._providers if p.name != "onvista"]
                providers_to_try = ([onvista] if onvista else []) + others
            else:
                providers_to_try = self._providers
        else:
            provider = self._by_name.get(source)
            if provider is None:
                raise ProviderExhaustedError(method, [source])
            providers_to_try = [provider]

        providers_tried: list[str] = []
        for provider in providers_to_try:
            providers_tried.append(provider.name)
            try:
                fn = getattr(provider, method)
                result = await fn(**kwargs)
            except Exception as exc:
                logger.debug("Provider {} raised exception for {}: {}", provider.name, method, exc)
                result = None

            if result is not None:
                logger.debug("Provider {} returned data for {}", provider.name, method)
                return result, provider.name

            logger.debug("Provider {} returned None for {}, trying next", provider.name, method)

        raise ProviderExhaustedError(method, providers_tried)
