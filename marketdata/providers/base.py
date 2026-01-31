from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ProviderPrice:
    symbol: str
    price: Decimal


class QuoteProvider:
    provider_name: str

    def fetch_latest_prices(self, symbols: list[str]) -> list[ProviderPrice]:
        """
        Fetch the latest available price per symbol.

        Must return only successfully fetched prices; caller can decide what to do with missing symbols.
        """
        raise NotImplementedError

