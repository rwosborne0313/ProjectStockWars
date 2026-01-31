from django.db import models


class PortfolioSnapshot(models.Model):
    participant = models.ForeignKey(
        "competitions.CompetitionParticipant",
        on_delete=models.CASCADE,
        related_name="portfolio_snapshots",
    )
    as_of = models.DateTimeField()

    cash_balance = models.DecimalField(max_digits=20, decimal_places=2)
    holdings_value = models.DecimalField(max_digits=20, decimal_places=2)
    total_value = models.DecimalField(max_digits=20, decimal_places=2)
    return_pct_since_start = models.DecimalField(max_digits=12, decimal_places=6)

    # Additional per-snapshot metrics used for charting on the dashboard.
    unrealized_pnl = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    realized_pnl_total = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    realized_pnl_today = models.DecimalField(max_digits=20, decimal_places=2, default=0)

    class Meta:
        indexes = [
            models.Index(fields=["-as_of"]),
            models.Index(fields=["participant", "-as_of"]),
        ]

    def __str__(self) -> str:
        return f"{self.participant_id}@{self.as_of.isoformat()}"
