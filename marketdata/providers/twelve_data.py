from __future__ import annotations

import os
from decimal import Decimal, InvalidOperation

import requests

from .base import ProviderPrice, QuoteProvider


class TwelveDataProvider(QuoteProvider):
    provider_name = "TWELVE_DATA"

    def __init__(self, api_key: str | None = None, session: requests.Session | None = None):
        self.api_key = api_key or os.environ.get("TWELVE_DATA_API_KEY")
        if not self.api_key:
            raise RuntimeError("Missing TWELVE_DATA_API_KEY")
        self.session = session or requests.Session()

    def fetch_latest_prices(self, symbols: list[str]) -> list[ProviderPrice]:
        # Twelve Data supports comma-separated symbols for some endpoints; the response shape can vary.
        # We implement a conservative per-symbol fetch to keep it predictable.
        results: list[ProviderPrice] = []
        for symbol in symbols:
            symbol = symbol.strip().upper()
            if not symbol:
                continue
            price = self._fetch_one(symbol)
            if price is not None:
                results.append(ProviderPrice(symbol=symbol, price=price))
        return results

    def fetch_quote(self, symbol: str) -> dict:
        """
        Fetch a full quote payload via Twelve Data `quote` endpoint.
        Docs: https://twelvedata.com/docs#quote
        """
        symbol = symbol.strip().upper()
        if not symbol:
            return {}
        url = "https://api.twelvedata.com/quote"
        resp = self.session.get(
            url,
            params={"symbol": symbol, "apikey": self.api_key},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        # Twelve Data uses {status:"error", code:..., message:...} on failures.
        if isinstance(data, dict) and data.get("status") == "error":
            return {}
        return data

    def _fetch_one(self, symbol: str) -> Decimal | None:
        url = "https://api.twelvedata.com/price"
        resp = self.session.get(
            url,
            params={"symbol": symbol, "apikey": self.api_key},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if "price" not in data:
            return None
        try:
            return Decimal(str(data["price"]))
        except (InvalidOperation, TypeError):
            return None

    def fetch_time_series(
        self,
        *,
        symbol: str,
        interval: str = "1day",
        outputsize: int = 90,
    ) -> dict:
        """
        Fetch OHLCV time series via Twelve Data `time_series` endpoint.
        Docs: https://twelvedata.com/docs#time-series
        """
        symbol = symbol.strip().upper()
        if not symbol:
            return {}
        url = "https://api.twelvedata.com/time_series"
        resp = self.session.get(
            url,
            params={
                "symbol": symbol,
                "interval": interval,
                "outputsize": outputsize,
                "format": "JSON",
                "apikey": self.api_key,
            },
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and data.get("status") == "error":
            return {}
        return data

