from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from competitions.models import Competition, CompetitionStatus, CompetitionParticipant, ParticipantStatus


class Command(BaseCommand):
    help = "Close and lock finished competitions (cron-friendly)."

    def handle(self, *args, **options):
        now = timezone.now()
        to_lock = Competition.objects.filter(
            status=CompetitionStatus.PUBLISHED,
            week_end_at__lt=now,
        )
        competition_ids = list(to_lock.values_list("id", flat=True))
        if not competition_ids:
            self.stdout.write("No competitions to lock.")
            return

        with transaction.atomic():
            Competition.objects.filter(id__in=competition_ids).update(status=CompetitionStatus.LOCKED)
            CompetitionParticipant.objects.filter(
                competition_id__in=competition_ids,
                status=ParticipantStatus.ACTIVE,
            ).update(status=ParticipantStatus.LOCKED)

        self.stdout.write(f"Locked {len(competition_ids)} competition(ies).")

