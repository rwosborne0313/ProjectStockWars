from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
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


class CompetitionType(models.TextChoices):
    STANDARD = "STANDARD", "Standard"
    ADVANCED = "ADVANCED", "Advanced"


class PriceSource(models.TextChoices):
    LAST = "LAST", "Last"
    BID = "BID", "Bid"
    ASK = "ASK", "Ask"


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
    competition_type = models.CharField(
        max_length=16, choices=CompetitionType.choices, default=CompetitionType.STANDARD
    )

    # Advanced rule configuration (nullable so STANDARD competitions are unaffected)
    max_single_symbol_pct = models.DecimalField(
        max_digits=6, decimal_places=4, blank=True, null=True
    )
    max_symbols = models.PositiveIntegerField(blank=True, null=True)
    min_symbols = models.PositiveIntegerField(blank=True, null=True)

    # Market order pricing configuration (Advanced)
    market_buy_price_source = models.CharField(
        max_length=8, choices=PriceSource.choices, default=PriceSource.LAST
    )

    # Future enhancement (greyed out for now in admin)
    allow_sell_short = models.BooleanField(default=False)

    # Auto-close configuration (Advanced; executed via cron-friendly command)
    auto_close_enabled = models.BooleanField(default=False)
    auto_close_price_source = models.CharField(
        max_length=8, choices=PriceSource.choices, default=PriceSource.LAST
    )
    synthetic_spread_bps = models.PositiveIntegerField(
        default=10,
        help_text="Synthetic bid/ask spread in basis points (1 bp = 0.01%). Used when Bid/Ask is selected.",
    )
    auto_close_processed_at = models.DateTimeField(blank=True, null=True)
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

    def clean(self) -> None:
        super().clean()

        if self.week_start_at and self.week_end_at and self.week_end_at <= self.week_start_at:
            raise ValidationError({"week_end_at": "End time must be after start time."})

        if self.min_symbols and self.max_symbols and self.min_symbols > self.max_symbols:
            raise ValidationError({"min_symbols": "Min symbols must be <= max symbols."})

        if self.max_single_symbol_pct is not None:
            if self.max_single_symbol_pct <= 0 or self.max_single_symbol_pct > 1:
                raise ValidationError({"max_single_symbol_pct": "Max % must be within (0, 1]."})

        if self.synthetic_spread_bps is not None and self.synthetic_spread_bps < 0:
            raise ValidationError({"synthetic_spread_bps": "Spread must be >= 0."})

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
