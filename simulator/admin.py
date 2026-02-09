from django.contrib import admin

from .models import (
    Basket,
    BasketItem,
    CashLedgerEntry,
    Order,
    Position,
    ScheduledBasketOrder,
    ScheduledBasketOrderLeg,
    TradeFill,
)


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


@admin.register(Basket)
class BasketAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "name", "updated_at", "created_at")
    search_fields = ("user__username", "name")
    list_filter = ("updated_at",)


@admin.register(BasketItem)
class BasketItemAdmin(admin.ModelAdmin):
    list_display = ("id", "basket", "instrument", "created_at")
    search_fields = ("basket__user__username", "basket__name", "instrument__symbol")
    list_filter = ("created_at",)


@admin.register(ScheduledBasketOrder)
class ScheduledBasketOrderAdmin(admin.ModelAdmin):
    list_display = ("id", "participant", "side", "total_amount", "status", "attempts", "created_at", "executed_at")
    list_filter = ("status", "side", "created_at")
    search_fields = ("participant__user__username", "basket_name")


@admin.register(ScheduledBasketOrderLeg)
class ScheduledBasketOrderLegAdmin(admin.ModelAdmin):
    list_display = ("id", "order", "instrument", "pct")
    search_fields = ("order__participant__user__username", "instrument__symbol", "order__basket_name")
