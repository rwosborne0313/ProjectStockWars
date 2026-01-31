from django.contrib import admin

from .models import CashLedgerEntry, Order, Position, TradeFill


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "participant",
        "instrument",
        "side",
        "order_type",
        "quantity",
        "limit_price",
        "status",
        "created_at",
        "submitted_price",
        "quote_as_of",
    )
    list_filter = ("status", "order_type", "side")
    search_fields = ("participant__user__username", "instrument__symbol")


@admin.register(TradeFill)
class TradeFillAdmin(admin.ModelAdmin):
    list_display = ("id", "order", "filled_at", "price", "quantity", "notional", "fee")
    list_filter = ("filled_at",)


@admin.register(CashLedgerEntry)
class CashLedgerEntryAdmin(admin.ModelAdmin):
    list_display = ("id", "participant", "as_of", "delta_amount", "reason", "reference_type", "reference_id")
    list_filter = ("reason",)
    search_fields = ("participant__user__username", "reference_type")


@admin.register(Position)
class PositionAdmin(admin.ModelAdmin):
    list_display = ("id", "participant", "instrument", "quantity", "avg_cost_basis", "updated_at")
    search_fields = ("participant__user__username", "instrument__symbol")
