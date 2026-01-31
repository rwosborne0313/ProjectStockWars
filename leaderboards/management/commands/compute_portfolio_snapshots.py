from __future__ import annotations

from decimal import Decimal
from datetime import timezone as py_timezone

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from competitions.models import Competition, CompetitionParticipant, CompetitionStatus, ParticipantStatus
from leaderboards.models import PortfolioSnapshot
from marketdata.models import Quote
from simulator.models import OrderSide, TradeFill


class Command(BaseCommand):
    help = "Compute portfolio value snapshots for participants in active competitions (cron-friendly)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--competition-id",
            type=int,
            default=None,
            help="Optionally restrict snapshot computation to a single competition id.",
        )

    def handle(self, *args, **options):
        now = timezone.now()
        competition_id = options.get("competition_id")

        competitions_qs = Competition.objects.filter(
            status=CompetitionStatus.PUBLISHED,
            week_start_at__lte=now,
            week_end_at__gte=now,
        )
        if competition_id:
            competitions_qs = competitions_qs.filter(id=competition_id)

        competitions = list(competitions_qs.only("id", "starting_cash"))
        if not competitions:
            self.stdout.write("No active competitions found.")
            return

        total_snapshots = 0
        with transaction.atomic():
            for comp in competitions:
                participants = list(
                    CompetitionParticipant.objects.filter(
                        competition=comp,
                        status=ParticipantStatus.ACTIVE,
                    ).prefetch_related("positions__instrument")
                )
                if not participants:
                    continue

                # Gather all instrument ids held by any participant
                instrument_ids: set[int] = set()
                for p in participants:
                    for pos in p.positions.all():
                        if pos.quantity <= 0:
                            continue
                        instrument_ids.add(pos.instrument_id)

                latest_prices: dict[int, Decimal] = {}
                if instrument_ids:
                    # Postgres-optimized: latest quote per instrument_id via DISTINCT ON.
                    latest_quotes = (
                        Quote.objects.filter(instrument_id__in=list(instrument_ids))
                        .order_by("instrument_id", "-as_of")
                        .distinct("instrument_id")
                        .only("instrument_id", "price")
                    )
                    latest_prices = {q.instrument_id: q.price for q in latest_quotes}

                as_of = timezone.now()
                for p in participants:
                    holdings_value = Decimal("0.00")
                    unrealized = Decimal("0.00")
                    for pos in p.positions.all():
                        if pos.quantity <= 0:
                            continue
                        price = latest_prices.get(pos.instrument_id)
                        if price is None:
                            continue
                        holdings_value += price * Decimal(pos.quantity)
                        unrealized += (price - pos.avg_cost_basis) * Decimal(pos.quantity)

                    total_value = p.cash_balance + holdings_value
                    return_pct = (
                        (total_value - p.starting_cash) / p.starting_cash
                        if p.starting_cash
                        else Decimal("0")
                    )

                    realized_total = (
                        TradeFill.objects.filter(order__participant=p, order__side=OrderSide.SELL)
                        .aggregate(total=Sum("realized_pnl"))["total"]
                        or Decimal("0.00")
                    )
                    local_now = timezone.localtime(as_of)
                    start_of_day_utc = local_now.replace(
                        hour=0, minute=0, second=0, microsecond=0
                    ).astimezone(py_timezone.utc)
                    realized_today = (
                        TradeFill.objects.filter(
                            order__participant=p,
                            order__side=OrderSide.SELL,
                            filled_at__gte=start_of_day_utc,
                        ).aggregate(total=Sum("realized_pnl"))["total"]
                        or Decimal("0.00")
                    )
                    PortfolioSnapshot.objects.create(
                        participant=p,
                        as_of=as_of,
                        cash_balance=p.cash_balance,
                        holdings_value=holdings_value,
                        total_value=total_value,
                        return_pct_since_start=return_pct,
                        unrealized_pnl=unrealized,
                        realized_pnl_total=realized_total,
                        realized_pnl_today=realized_today,
                    )
                    total_snapshots += 1

        self.stdout.write(f"Created {total_snapshots} portfolio snapshot(s).")

