from django.contrib import admin

from .models import PortfolioSnapshot


@admin.register(PortfolioSnapshot)
class PortfolioSnapshotAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "participant",
        "as_of",
        "cash_balance",
        "holdings_value",
        "total_value",
        "return_pct_since_start",
        "unrealized_pnl",
        "realized_pnl_today",
        "realized_pnl_total",
    )
    list_filter = ("as_of",)
    search_fields = ("participant__user__username", "participant__competition__title")
