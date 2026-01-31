from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone as py_timezone
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from competitions.models import CompetitionParticipant
from leaderboards.models import PortfolioSnapshot
from marketdata.models import Quote
from simulator.models import OrderSide, TradeFill


@dataclass(frozen=True)
class SnapshotMetrics:
    cash_balance: Decimal
    holdings_value: Decimal
    total_value: Decimal
    return_pct_since_start: Decimal
    unrealized_pnl: Decimal
    realized_pnl_total: Decimal
    realized_pnl_today: Decimal


def compute_snapshot_metrics(*, participant: CompetitionParticipant, as_of=None) -> SnapshotMetrics:
    as_of = as_of or timezone.now()

    positions = list(participant.positions.filter(quantity__gt=0).values("instrument_id", "quantity", "avg_cost_basis"))
    instrument_ids = sorted({p["instrument_id"] for p in positions})

    latest_prices: dict[int, Decimal] = {}
    if instrument_ids:
        latest_quotes = (
            Quote.objects.filter(instrument_id__in=instrument_ids)
            .order_by("instrument_id", "-as_of")
            .distinct("instrument_id")
            .only("instrument_id", "price")
        )
        latest_prices = {q.instrument_id: q.price for q in latest_quotes}

    holdings_value = Decimal("0.00")
    unrealized = Decimal("0.00")
    for pos in positions:
        price = latest_prices.get(pos["instrument_id"])
        if price is None:
            continue
        qty = Decimal(pos["quantity"])
        avg_cost = pos["avg_cost_basis"]
        holdings_value += price * qty
        unrealized += (price - avg_cost) * qty

    cash_balance = participant.cash_balance
    total_value = cash_balance + holdings_value
    return_pct = (
        (total_value - participant.starting_cash) / participant.starting_cash
        if participant.starting_cash
        else Decimal("0")
    )

    realized_total = (
        TradeFill.objects.filter(order__participant=participant, order__side=OrderSide.SELL)
        .aggregate(total=Sum("realized_pnl"))["total"]
        or Decimal("0.00")
    )
    local_now = timezone.localtime(as_of)
    start_of_day_utc = local_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(
        py_timezone.utc
    )
    realized_today = (
        TradeFill.objects.filter(
            order__participant=participant,
            order__side=OrderSide.SELL,
            filled_at__gte=start_of_day_utc,
        ).aggregate(total=Sum("realized_pnl"))["total"]
        or Decimal("0.00")
    )

    return SnapshotMetrics(
        cash_balance=cash_balance,
        holdings_value=holdings_value,
        total_value=total_value,
        return_pct_since_start=return_pct,
        unrealized_pnl=unrealized,
        realized_pnl_total=realized_total,
        realized_pnl_today=realized_today,
    )


def create_portfolio_snapshot(*, participant: CompetitionParticipant, as_of=None) -> PortfolioSnapshot:
    as_of = as_of or timezone.now()
    metrics = compute_snapshot_metrics(participant=participant, as_of=as_of)
    return PortfolioSnapshot.objects.create(
        participant=participant,
        as_of=as_of,
        cash_balance=metrics.cash_balance,
        holdings_value=metrics.holdings_value,
        total_value=metrics.total_value,
        return_pct_since_start=metrics.return_pct_since_start,
        unrealized_pnl=metrics.unrealized_pnl,
        realized_pnl_total=metrics.realized_pnl_total,
        realized_pnl_today=metrics.realized_pnl_today,
    )

