from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone


class CompetitionStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    PUBLISHED = "PUBLISHED", "Published"
    LOCKED = "LOCKED", "Locked"
    ARCHIVED = "ARCHIVED", "Archived"


class ParticipantStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Active"
    DISQUALIFIED = "DISQUALIFIED", "Disqualified"
    LOCKED = "LOCKED", "Locked"


class ValuationPolicy(models.TextChoices):
    LATEST_CACHED = "LATEST_CACHED", "Latest cached quote"


class Competition(models.Model):
    title = models.CharField(max_length=200)
    week_start_at = models.DateTimeField()
    week_end_at = models.DateTimeField()

    sponsor = models.ForeignKey(
        "sponsors.Sponsor", on_delete=models.PROTECT, related_name="competitions"
    )

    theme_name = models.CharField(max_length=200, blank=True)
    theme_primary_color = models.CharField(max_length=32, blank=True)
    hero_image = models.FileField(upload_to="competitions/hero/", blank=True, null=True)

    starting_cash = models.DecimalField(
        max_digits=20, decimal_places=2, default=Decimal("10000000.00")
    )

    first_place_cash_prize = models.DecimalField(
        max_digits=20, decimal_places=2, blank=True, null=True
    )
    second_place_cash_prize = models.DecimalField(
        max_digits=20, decimal_places=2, blank=True, null=True
    )
    third_place_cash_prize = models.DecimalField(
        max_digits=20, decimal_places=2, blank=True, null=True
    )
    entry_fee = models.DecimalField(max_digits=20, decimal_places=2, blank=True, null=True)
    non_cash_prize = models.CharField(max_length=255, blank=True, null=True)

    rules_markdown = models.TextField(blank=True)
    valuation_policy = models.CharField(
        max_length=32, choices=ValuationPolicy.choices, default=ValuationPolicy.LATEST_CACHED
    )
    status = models.CharField(
        max_length=16, choices=CompetitionStatus.choices, default=CompetitionStatus.DRAFT
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "week_start_at", "week_end_at"]),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def is_active(self) -> bool:
        now = timezone.now()
        return (
            self.status == CompetitionStatus.PUBLISHED
            and self.week_start_at <= now <= self.week_end_at
        )


class CompetitionParticipant(models.Model):
    competition = models.ForeignKey(
        Competition, on_delete=models.CASCADE, related_name="participants"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="competition_participations"
    )
    joined_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(
        max_length=16, choices=ParticipantStatus.choices, default=ParticipantStatus.ACTIVE
    )

    starting_cash = models.DecimalField(max_digits=20, decimal_places=2)
    cash_balance = models.DecimalField(max_digits=20, decimal_places=2)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["competition", "user"], name="uniq_competition_user"
            ),
            models.CheckConstraint(
                check=models.Q(cash_balance__gte=0), name="participant_cash_nonnegative"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.competition_id}:{self.user_id}"
