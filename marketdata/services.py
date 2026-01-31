from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from marketdata.models import Instrument, Quote
from marketdata.providers import TwelveDataProvider


_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,15}$")


def normalize_symbol(raw: str) -> str:
    sym = (raw or "").strip().upper()
    if not sym or not _SYMBOL_RE.match(sym):
        raise ValueError("Invalid symbol format.")
    return sym


def get_or_create_instrument_by_symbol(raw_symbol: str) -> Instrument:
    symbol = normalize_symbol(raw_symbol)
    inst, _ = Instrument.objects.get_or_create(symbol=symbol, defaults={"name": ""})
    return inst


def fetch_and_store_latest_quote(*, instrument: Instrument) -> Quote | None:
    """
    Fetch latest quote from provider and store a Quote row.
    Returns the created Quote or None if fetch fails.
    """
    try:
        provider = TwelveDataProvider()
        data = provider.fetch_quote(instrument.symbol)
        if not data:
            return None

        def _d(key: str) -> Decimal | None:
            raw = data.get(key)
            if raw in (None, ""):
                return None
            try:
                return Decimal(str(raw))
            except (InvalidOperation, TypeError):
                return None

        def _d_nested(*keys: str) -> Decimal | None:
            cur = data
            for k in keys:
                if not isinstance(cur, dict):
                    return None
                cur = cur.get(k)
            if cur in (None, ""):
                return None
            try:
                return Decimal(str(cur))
            except (InvalidOperation, TypeError):
                return None

        # Twelve Data quote payloads use `close` as the latest/last price in many examples.
        # Some responses may include `price` instead; accept either.
        last = _d("close") or _d("price")
        if last is None:
            return None

        return Quote.objects.create(
            instrument=instrument,
            as_of=timezone.now(),
            price=last,
            open=_d("open"),
            high=_d("high"),
            low=_d("low"),
            close=_d("close"),
            volume=int(data.get("volume")) if str(data.get("volume") or "").isdigit() else None,
            change=_d("change"),
            percent_change=_d("percent_change"),
            fifty_two_week_high=_d_nested("fifty_two_week", "high"),
            fifty_two_week_low=_d_nested("fifty_two_week", "low"),
            provider_name=provider.provider_name,
        )
    except Exception:
        return None

