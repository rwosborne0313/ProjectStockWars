from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from competitions.models import Competition, CompetitionParticipant, CompetitionStatus, ParticipantStatus
from marketdata.models import Instrument, Quote, WatchlistItem
from marketdata.providers import TwelveDataProvider
from simulator.models import Position


class Command(BaseCommand):
    help = "Fetch latest quotes for symbols actually used (watchlists + positions) in active competitions (cron-friendly)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--competition-id",
            type=int,
            default=None,
            help="Optionally restrict fetching to a single competition id.",
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

        competition_ids = list(competitions_qs.values_list("id", flat=True))
        if not competition_ids:
            self.stdout.write("No active competitions found.")
            return

        participants = list(
            CompetitionParticipant.objects.filter(
                competition_id__in=competition_ids,
                status=ParticipantStatus.ACTIVE,
            ).only("id", "user_id", "competition_id")
        )
        participant_ids = [p.id for p in participants]
        user_ids = list({p.user_id for p in participants})

        instrument_ids = set(
            Position.objects.filter(participant_id__in=participant_ids).values_list(
                "instrument_id", flat=True
            )
        )
        instrument_ids |= set(
            WatchlistItem.objects.filter(user_id__in=user_ids).values_list(
                "instrument_id", flat=True
            )
        )

        if not instrument_ids:
            self.stdout.write("No positions or watchlists found for active competitions.")
            return

        instruments_by_symbol = {
            sym.upper(): iid
            for iid, sym in Instrument.objects.filter(id__in=list(instrument_ids)).values_list(
                "id", "symbol"
            )
        }

        symbols = sorted(instruments_by_symbol.keys())
        if not symbols:
            self.stdout.write("No symbols found.")
            return

        provider = TwelveDataProvider()
        fetched = provider.fetch_latest_prices(symbols)

        prices_by_symbol = {p.symbol.upper(): p.price for p in fetched}
        missing = [s for s in symbols if s not in prices_by_symbol]

        created_count = 0

        # We store quotes once per instrument.
        with transaction.atomic():
            as_of = timezone.now()
            for symbol, instrument_id in instruments_by_symbol.items():
                if symbol not in prices_by_symbol:
                    continue
                Quote.objects.create(
                    instrument_id=instrument_id,
                    as_of=as_of,
                    price=prices_by_symbol[symbol],
                    provider_name=provider.provider_name,
                )
                created_count += 1

        self.stdout.write(
            f"Fetched {len(prices_by_symbol)}/{len(symbols)} symbols, stored {created_count} quotes."
        )
        if missing:
            self.stdout.write(f"Missing: {', '.join(missing[:50])}{'...' if len(missing) > 50 else ''}")

