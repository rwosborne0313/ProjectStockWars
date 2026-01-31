from django.contrib import admin

from .models import Instrument, Quote, WatchlistItem


@admin.register(Instrument)
class InstrumentAdmin(admin.ModelAdmin):
    list_display = ("id", "symbol", "name", "exchange", "asset_type", "updated_at")
    list_filter = ("asset_type", "exchange")
    search_fields = ("symbol", "name", "exchange")


@admin.register(Quote)
class QuoteAdmin(admin.ModelAdmin):
    list_display = ("id", "instrument", "as_of", "price", "provider_name")
    list_filter = ("provider_name",)
    search_fields = ("instrument__symbol",)
    readonly_fields = (
        "instrument",
        "as_of",
        "price",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "provider_name",
    )


@admin.register(WatchlistItem)
class WatchlistItemAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "instrument", "created_at")
    search_fields = ("user__username", "instrument__symbol")
