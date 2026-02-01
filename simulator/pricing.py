from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from competitions.models import PriceSource


def derive_price_from_source(
    *,
    last_price: Decimal,
    price_source: str,
    synthetic_spread_bps: int,
) -> Decimal:
    """
    Convert a LAST price into a synthetic BID/ASK using a configurable spread.

    - LAST: last_price
    - BID:  last_price * (1 - spread_bps/10000)
    - ASK:  last_price * (1 + spread_bps/10000)
    """
    if last_price is None:
        raise ValueError("last_price is required")
    if synthetic_spread_bps is None or synthetic_spread_bps < 0:
        raise ValueError("synthetic_spread_bps must be >= 0")

    src = price_source or PriceSource.LAST
    spread = Decimal(synthetic_spread_bps) / Decimal("10000")

    if src == PriceSource.LAST:
        return last_price
    if src == PriceSource.BID:
        return (last_price * (Decimal("1") - spread)).quantize(
            Decimal("0.000001"), rounding=ROUND_HALF_UP
        )
    if src == PriceSource.ASK:
        return (last_price * (Decimal("1") + spread)).quantize(
            Decimal("0.000001"), rounding=ROUND_HALF_UP
        )

    # Unknown config; safest fallback is LAST.
    return last_price

