from django.contrib import admin

from .models import Instrument, Quote, Watchlist, WatchlistItem


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
    list_display = ("id", "watchlist", "watchlist_user", "instrument", "created_at")
    search_fields = ("watchlist__user__username", "watchlist__name", "instrument__symbol")

    @admin.display(description="User")
    def watchlist_user(self, obj: WatchlistItem):
        return obj.watchlist.user if obj.watchlist_id else None


@admin.register(Watchlist)
class WatchlistAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "name", "industry_label", "updated_at")
    search_fields = ("user__username", "user__email", "name", "industry_label")
