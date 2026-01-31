from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class OrderSide(models.TextChoices):
    BUY = "BUY", "Buy"
    SELL = "SELL", "Sell"


class OrderType(models.TextChoices):
    MARKET = "MARKET", "Market"
    LIMIT = "LIMIT", "Limit"


class OrderStatus(models.TextChoices):
    SUBMITTED = "SUBMITTED", "Submitted"
    FILLED = "FILLED", "Filled"
    REJECTED = "REJECTED", "Rejected"
    CANCELLED = "CANCELLED", "Cancelled"


class CashLedgerReason(models.TextChoices):
    STARTING_CASH = "STARTING_CASH", "Starting cash"
    TRADE_BUY = "TRADE_BUY", "Trade buy"
    TRADE_SELL = "TRADE_SELL", "Trade sell"
    ADJUSTMENT = "ADJUSTMENT", "Adjustment"


class Order(models.Model):
    participant = models.ForeignKey(
        "competitions.CompetitionParticipant", on_delete=models.CASCADE, related_name="orders"
    )
    instrument = models.ForeignKey(
        "marketdata.Instrument", on_delete=models.PROTECT, related_name="+"
    )
    side = models.CharField(max_length=8, choices=OrderSide.choices)
    order_type = models.CharField(max_length=8, choices=OrderType.choices)
    quantity = models.PositiveIntegerField()

    limit_price = models.DecimalField(
        max_digits=20, decimal_places=6, blank=True, null=True
    )

    created_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(
        max_length=16, choices=OrderStatus.choices, default=OrderStatus.SUBMITTED
    )

    submitted_price = models.DecimalField(
        max_digits=20, decimal_places=6, blank=True, null=True
    )
    quote_as_of = models.DateTimeField(blank=True, null=True)
    reject_reason = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["participant", "-created_at"]),
            models.Index(fields=["instrument", "-created_at"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(order_type=OrderType.MARKET, limit_price__isnull=True)
                    | models.Q(order_type=OrderType.LIMIT, limit_price__isnull=False)
                ),
                name="order_limit_price_required_for_limit",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        if self.order_type == OrderType.LIMIT:
            if self.limit_price is None:
                raise ValidationError({"limit_price": "Limit price is required for LIMIT orders."})
            if self.limit_price <= 0:
                raise ValidationError({"limit_price": "Limit price must be > 0."})
        if self.order_type == OrderType.MARKET and self.limit_price is not None:
            raise ValidationError({"limit_price": "Market orders must not set a limit price."})

    def __str__(self) -> str:
        return f"{self.participant_id}:{self.side}:{self.instrument_id}:{self.quantity}"


class TradeFill(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="fills")
    filled_at = models.DateTimeField(default=timezone.now)
    price = models.DecimalField(max_digits=20, decimal_places=6)
    quantity = models.PositiveIntegerField()
    notional = models.DecimalField(max_digits=20, decimal_places=2)
    fee = models.DecimalField(max_digits=20, decimal_places=2, default=Decimal("0.00"))
    realized_pnl = models.DecimalField(max_digits=20, decimal_places=2, default=Decimal("0.00"))

    class Meta:
        indexes = [
            models.Index(fields=["-filled_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.order_id}@{self.price}x{self.quantity}"


class CashLedgerEntry(models.Model):
    participant = models.ForeignKey(
        "competitions.CompetitionParticipant", on_delete=models.CASCADE, related_name="cash_ledger_entries"
    )
    as_of = models.DateTimeField(default=timezone.now)
    delta_amount = models.DecimalField(max_digits=20, decimal_places=2)
    reason = models.CharField(max_length=24, choices=CashLedgerReason.choices)

    reference_type = models.CharField(max_length=32, blank=True)
    reference_id = models.BigIntegerField(blank=True, null=True)
    memo = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["participant", "-as_of"]),
            models.Index(fields=["participant", "reference_type", "reference_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.participant_id}:{self.delta_amount}:{self.reason}"


class Position(models.Model):
    participant = models.ForeignKey(
        "competitions.CompetitionParticipant", on_delete=models.CASCADE, related_name="positions"
    )
    instrument = models.ForeignKey(
        "marketdata.Instrument", on_delete=models.PROTECT, related_name="+"
    )
    quantity = models.PositiveIntegerField(default=0)
    avg_cost_basis = models.DecimalField(
        max_digits=20, decimal_places=6, default=Decimal("0.0")
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["participant", "instrument"], name="uniq_position_participant_instrument"
            )
        ]

    def __str__(self) -> str:
        return f"{self.participant_id}:{self.instrument_id}:{self.quantity}"
