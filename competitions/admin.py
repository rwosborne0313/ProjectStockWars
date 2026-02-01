from django.contrib import admin
from django.db.models import Sum
from django.utils import timezone

from simulator.models import CashLedgerEntry

from .models import (
    Competition,
    CompetitionParticipant,
    CompetitionStatus,
    ParticipantStatus,
)


@admin.action(description="Publish selected competitions")
def publish_competitions(modeladmin, request, queryset):
    queryset.update(status=CompetitionStatus.PUBLISHED, updated_at=timezone.now())


@admin.action(description="Lock selected competitions")
def lock_competitions(modeladmin, request, queryset):
    queryset.update(status=CompetitionStatus.LOCKED, updated_at=timezone.now())


@admin.action(description="Archive selected competitions")
def archive_competitions(modeladmin, request, queryset):
    queryset.update(status=CompetitionStatus.ARCHIVED, updated_at=timezone.now())


@admin.register(Competition)
class CompetitionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "title",
        "sponsor",
        "status",
        "competition_type",
        "week_start_at",
        "week_end_at",
        "starting_cash",
        "first_place_cash_prize",
        "entry_fee",
    )
    list_filter = ("status", "sponsor")
    search_fields = ("title", "sponsor__name")
    actions = (publish_competitions, lock_competitions, archive_competitions)
    readonly_fields = ("allow_sell_short", "auto_close_processed_at")

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "title",
                    "sponsor",
                    "status",
                    "competition_type",
                    "week_start_at",
                    "week_end_at",
                    "starting_cash",
                    "entry_fee",
                )
            },
        ),
        (
            "Prizes",
            {
                "fields": (
                    "first_place_cash_prize",
                    "second_place_cash_prize",
                    "third_place_cash_prize",
                    "non_cash_prize",
                )
            },
        ),
        (
            "Theme & content",
            {"fields": ("theme_name", "theme_primary_color", "hero_image", "rules_markdown")},
        ),
        ("Valuation", {"fields": ("valuation_policy",)}),
        (
            "Advanced rules",
            {
                "fields": (
                    "max_symbols",
                    "min_symbols",
                    "max_single_symbol_pct",
                    "market_buy_price_source",
                    "synthetic_spread_bps",
                    "allow_sell_short",
                    "auto_close_enabled",
                    "auto_close_price_source",
                    "auto_close_processed_at",
                ),
                "description": (
                    "Advanced rules apply only when Competition type is set to Advanced. "
                    "Sell short is coming soon (disabled). Bid/Ask pricing uses a synthetic spread."
                ),
            },
        ),
    )


@admin.action(description="Disqualify selected participants")
def disqualify_participants(modeladmin, request, queryset):
    queryset.update(status=ParticipantStatus.DISQUALIFIED, updated_at=timezone.now())


@admin.action(description="Re-activate selected participants")
def activate_participants(modeladmin, request, queryset):
    queryset.update(status=ParticipantStatus.ACTIVE, updated_at=timezone.now())


@admin.action(description="Recompute cash_balance from ledger (selected participants)")
def recompute_cash_from_ledger(modeladmin, request, queryset):
    for participant in queryset.select_related("competition", "user"):
        total = (
            CashLedgerEntry.objects.filter(participant=participant).aggregate(
                total=Sum("delta_amount")
            )["total"]
            or 0
        )
        participant.cash_balance = total
        participant.save(update_fields=["cash_balance", "updated_at"])


@admin.register(CompetitionParticipant)
class CompetitionParticipantAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "competition",
        "user",
        "status",
        "starting_cash",
        "cash_balance",
        "joined_at",
    )
    list_filter = ("status", "competition")
    search_fields = ("user__username", "user__email", "competition__title")
    actions = (activate_participants, disqualify_participants, recompute_cash_from_ledger)
