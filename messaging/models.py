from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class AdminMessage(models.Model):
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="admin_messages_created"
    )
    subject = models.CharField(max_length=200)
    body = models.TextField()

    send_to_all = models.BooleanField(default=False)
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="admin_messages_received",
        blank=True,
        null=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        indexes = [
            models.Index(fields=["-created_at"]),
            models.Index(fields=["-sent_at"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(send_to_all=True, recipient__isnull=True)
                    | models.Q(send_to_all=False, recipient__isnull=False)
                ),
                name="adminmessage_send_to_all_xor_recipient",
            )
        ]

    def clean(self) -> None:
        super().clean()
        if bool(self.send_to_all) == bool(self.recipient_id):
            raise ValidationError("Select either a recipient or send to all users (not both).")

    def __str__(self) -> str:
        return f"{self.subject} ({self.sent_at or 'draft'})"


class MessageDelivery(models.Model):
    message = models.ForeignKey(AdminMessage, on_delete=models.CASCADE, related_name="deliveries")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="message_deliveries")
    sent_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["message", "user"], name="uniq_message_delivery_message_user"),
        ]
        indexes = [
            models.Index(fields=["user", "-sent_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.user_id}:{self.message_id}@{self.sent_at}"

