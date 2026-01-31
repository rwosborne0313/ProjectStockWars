from django.conf import settings
from django.db import models


class AssetType(models.TextChoices):
    EQUITY = "EQUITY", "Equity"
    ETF = "ETF", "ETF"


class Instrument(models.Model):
    symbol = models.CharField(max_length=16, unique=True)
    name = models.CharField(max_length=200, blank=True)
    exchange = models.CharField(max_length=50, blank=True)
    asset_type = models.CharField(
        max_length=16, choices=AssetType.choices, default=AssetType.EQUITY
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.symbol


class Quote(models.Model):
    instrument = models.ForeignKey(
        Instrument, on_delete=models.CASCADE, related_name="quotes"
    )
    as_of = models.DateTimeField()

    # canonical price used by the simulator
    price = models.DecimalField(max_digits=20, decimal_places=6)

    open = models.DecimalField(max_digits=20, decimal_places=6, blank=True, null=True)
    high = models.DecimalField(max_digits=20, decimal_places=6, blank=True, null=True)
    low = models.DecimalField(max_digits=20, decimal_places=6, blank=True, null=True)
    close = models.DecimalField(max_digits=20, decimal_places=6, blank=True, null=True)
    volume = models.BigIntegerField(blank=True, null=True)

    change = models.DecimalField(max_digits=20, decimal_places=6, blank=True, null=True)
    percent_change = models.DecimalField(max_digits=20, decimal_places=6, blank=True, null=True)

    fifty_two_week_high = models.DecimalField(max_digits=20, decimal_places=6, blank=True, null=True)
    fifty_two_week_low = models.DecimalField(max_digits=20, decimal_places=6, blank=True, null=True)

    provider_name = models.CharField(max_length=50)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["instrument", "as_of", "provider_name"],
                name="uniq_quote_instrument_asof_provider",
            ),
        ]
        indexes = [
            models.Index(fields=["instrument", "-as_of"]),
            models.Index(fields=["provider_name", "as_of"]),
        ]

    def __str__(self) -> str:
        return f"{self.instrument.symbol}@{self.as_of.isoformat()}"


class WatchlistItem(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="watchlist_items"
    )
    instrument = models.ForeignKey(
        Instrument, on_delete=models.CASCADE, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "instrument"], name="uniq_watchlist_user_instrument"
            )
        ]

    def __str__(self) -> str:
        return f"{self.user_id}:{self.instrument.symbol}"
