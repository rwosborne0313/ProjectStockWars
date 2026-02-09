import io

from django.contrib import admin
from django.contrib import messages
from django.core.management import call_command
from django.core.exceptions import PermissionDenied
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


def _can_run_ops(request) -> bool:
    """
    Gate operational admin actions behind an explicit permission check.
    Default: allow staff with change permission on Competition (or superuser).
    """
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return False
    if not user.is_active or not user.is_staff:
        return False
    if user.is_superuser:
        return True
    return user.has_perm("competitions.change_competition")


def _run_command_and_capture(*cmd_args, **cmd_kwargs) -> str:
    """
    Run a Django management command and return captured stdout.
    """
    buf = io.StringIO()
    call_command(*cmd_args, stdout=buf, stderr=buf, **cmd_kwargs)
    return (buf.getvalue() or "").strip()


@admin.action(description="Ops: activate queued participants (start competitions)")
def ops_activate_queued_participants(modeladmin, request, queryset):
    if not _can_run_ops(request):
        raise PermissionDenied
    out = _run_command_and_capture("activate_queued_participants")
    msg = out.splitlines()[-1] if out else "activate_queued_participants completed."
    modeladmin.message_user(request, msg, level=messages.SUCCESS)


@admin.action(description="Ops: execute scheduled basket orders")
def ops_execute_scheduled_basket_orders(modeladmin, request, queryset):
    if not _can_run_ops(request):
        raise PermissionDenied
    out = _run_command_and_capture("execute_scheduled_basket_orders")
    msg = out.splitlines()[-1] if out else "execute_scheduled_basket_orders completed."
    modeladmin.message_user(request, msg, level=messages.SUCCESS)


@admin.action(description="Ops: market open sequence (activate queue → execute scheduled baskets)")
def ops_market_open_sequence(modeladmin, request, queryset):
    if not _can_run_ops(request):
        raise PermissionDenied
    out1 = _run_command_and_capture("activate_queued_participants")
    out2 = _run_command_and_capture("execute_scheduled_basket_orders")
    tail = []
    if out1:
        tail.append(out1.splitlines()[-1])
    if out2:
        tail.append(out2.splitlines()[-1])
    modeladmin.message_user(
        request,
        " | ".join(tail) if tail else "Market open sequence completed.",
        level=messages.SUCCESS,
    )


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
    actions = (
        publish_competitions,
        lock_competitions,
        archive_competitions,
        ops_activate_queued_participants,
        ops_execute_scheduled_basket_orders,
        ops_market_open_sequence,
    )
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
