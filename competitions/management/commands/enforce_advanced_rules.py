from __future__ import annotations

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
from simulator.models import Position


class Command(BaseCommand):
    help = "Enforce Advanced competition rules (min symbols) (cron-friendly)."

    def handle(self, *args, **options):
        now = timezone.now()

        with transaction.atomic():
            competitions = list(
                Competition.objects.select_for_update()
                .filter(
                    competition_type=CompetitionType.ADVANCED,
                    status=CompetitionStatus.PUBLISHED,
                    week_start_at__lte=now,
                    week_end_at__gte=now,
                    min_symbols__isnull=False,
                )
                .order_by("id")
            )

            if not competitions:
                self.stdout.write("No active Advanced competitions to enforce.")
                return

            total_disqualified = 0

            for competition in competitions:
                min_symbols = int(competition.min_symbols or 0)
                if min_symbols <= 0:
                    continue

                participants = list(
                    CompetitionParticipant.objects.select_for_update()
                    .filter(competition=competition, status=ParticipantStatus.ACTIVE)
                    .order_by("id")
                )

                for participant in participants:
                    held_symbols = (
                        Position.objects.filter(participant=participant, quantity__gt=0).count()
                    )
                    if held_symbols < min_symbols:
                        participant.status = ParticipantStatus.DISQUALIFIED
                        participant.save(update_fields=["status", "updated_at"])
                        total_disqualified += 1

            self.stdout.write(f"Disqualified {total_disqualified} participant(s).")

