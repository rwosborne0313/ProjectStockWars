from __future__ import annotations

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from competitions.models import (
    Competition,
    CompetitionParticipant,
    CompetitionStatus,
    CompetitionType,
    ParticipantStatus,
)
from marketdata.services import fetch_and_store_latest_quote
from simulator.models import (
    CashLedgerEntry,
    CashLedgerReason,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    TradeFill,
)
from simulator.pricing import derive_price_from_source
from simulator.services import _quantize_money


class Command(BaseCommand):
    help = "Auto-close positions for ended Advanced competitions (cron-friendly, idempotent)."

    def handle(self, *args, **options):
        now = timezone.now()

        with transaction.atomic():
            # Include LOCKED as well so we don't miss auto-close if another cron locks first.
            competitions = list(
                Competition.objects.select_for_update()
                .filter(
                    competition_type=CompetitionType.ADVANCED,
                    auto_close_enabled=True,
                    auto_close_processed_at__isnull=True,
                    week_end_at__lte=now,
                    status__in=[CompetitionStatus.PUBLISHED, CompetitionStatus.LOCKED],
                )
                .order_by("week_end_at")
            )
            if not competitions:
                self.stdout.write("No competitions to auto-close.")
                return

            total_positions_closed = 0
            total_competitions_processed = 0

            for competition in competitions:
                end_at = competition.week_end_at
                had_failure = False

                participants = CompetitionParticipant.objects.select_for_update().filter(
                    competition=competition,
                    status=ParticipantStatus.ACTIVE,
                )

                for participant in participants:
                    positions = list(
                        Position.objects.select_for_update()
                        .filter(participant=participant, quantity__gt=0)
                        .select_related("instrument")
                    )
                    if not positions:
                        continue

                    for position in positions:
                        qty = int(position.quantity)
                        if qty <= 0:
                            continue

                        inst = position.instrument

                        # Refresh quote so we have a recent LAST to synthesize bid/ask from.
                        latest_quote = fetch_and_store_latest_quote(instrument=inst)
                        if latest_quote is None:
                            # If quote refresh fails, skip this position (do not mark competition processed).
                            self.stdout.write(
                                f"QUOTE_REFRESH_FAILED competition={competition.id} participant={participant.id} symbol={inst.symbol}"
                            )
                            had_failure = True
                            continue

                        fill_price = derive_price_from_source(
                            last_price=latest_quote.price,
                            price_source=competition.auto_close_price_source,
                            synthetic_spread_bps=int(competition.synthetic_spread_bps or 0),
                        )
                        notional = _quantize_money(fill_price * Decimal(qty))

                        order = Order.objects.create(
                            participant=participant,
                            instrument=inst,
                            side=OrderSide.SELL,
                            order_type=OrderType.MARKET,
                            quantity=qty,
                            status=OrderStatus.FILLED,
                            submitted_price=fill_price,
                            quote_as_of=latest_quote.as_of,
                        )

                        realized_pnl = _quantize_money(
                            (fill_price - (position.avg_cost_basis or Decimal("0"))) * Decimal(qty)
                        )

                        TradeFill.objects.create(
                            order=order,
                            filled_at=end_at,
                            price=fill_price,
                            quantity=qty,
                            notional=notional,
                            realized_pnl=realized_pnl,
                        )

                        # Apply position + cash changes
                        position.delete()

                        participant.cash_balance = participant.cash_balance + notional
                        participant.save(update_fields=["cash_balance", "updated_at"])

                        CashLedgerEntry.objects.create(
                            participant=participant,
                            as_of=end_at,
                            delta_amount=notional,
                            reason=CashLedgerReason.TRADE_SELL,
                            reference_type="ORDER",
                            reference_id=order.id,
                            memo="AUTO_CLOSE_AT_COMPETITION_END",
                        )

                        total_positions_closed += 1

                    # Record final snapshot at end time (best-effort)
                    try:
                        from leaderboards.services import create_portfolio_snapshot

                        create_portfolio_snapshot(participant=participant, as_of=end_at)
                    except Exception:
                        pass

                # Only mark processed if every position was handled successfully.
                if not had_failure:
                    competition.auto_close_processed_at = now
                    competition.save(update_fields=["auto_close_processed_at"])
                    total_competitions_processed += 1

            self.stdout.write(
                f"Auto-closed {total_positions_closed} position(s) across {total_competitions_processed} competition(ies)."
            )

