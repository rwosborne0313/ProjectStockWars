from __future__ import annotations

from django import forms
from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import AdminMessage, MessageDelivery


class AdminMessageForm(forms.ModelForm):
    class Meta:
        model = AdminMessage
        fields = ["subject", "body", "send_to_all", "recipient"]

    def clean(self):
        cleaned = super().clean()
        send_to_all = bool(cleaned.get("send_to_all"))
        recipient = cleaned.get("recipient")
        if send_to_all == bool(recipient):
            raise ValidationError("Select either a recipient or send to all users (not both).")
        return cleaned


@admin.register(AdminMessage)
class AdminMessageAdmin(admin.ModelAdmin):
    form = AdminMessageForm
    list_display = ("subject", "send_to_all", "recipient", "created_by", "sent_at", "created_at")
    list_filter = ("send_to_all", "sent_at", "created_at")
    search_fields = ("subject", "body", "recipient__username", "recipient__email")
    ordering = ("-created_at",)

    def get_readonly_fields(self, request, obj=None):
        ro = ["created_by", "created_at", "sent_at"]
        if obj and obj.sent_at:
            ro.extend(["subject", "body", "send_to_all", "recipient"])
        return ro

    @transaction.atomic
    def save_model(self, request, obj, form, change):
        is_new_send = obj.pk is None or obj.sent_at is None

        if obj.pk is None and not obj.created_by_id:
            obj.created_by = request.user

        super().save_model(request, obj, form, change)

        if not is_new_send:
            return

        delivered = 0
        send_ts = timezone.now()

        if obj.send_to_all:
            User = get_user_model()
            user_ids = list(User.objects.values_list("id", flat=True))
            deliveries = [MessageDelivery(message=obj, user_id=uid, sent_at=send_ts) for uid in user_ids]
            MessageDelivery.objects.bulk_create(deliveries, ignore_conflicts=True, batch_size=2000)
            delivered = len(user_ids)
        else:
            if not obj.recipient_id:
                raise ValidationError("Recipient is required when send_to_all is false.")
            MessageDelivery.objects.get_or_create(
                message=obj, user_id=obj.recipient_id, defaults={"sent_at": send_ts}
            )
            delivered = 1

        obj.sent_at = send_ts
        obj.save(update_fields=["sent_at"])

        self.message_user(
            request,
            f"Message sent. Delivered to {delivered} user(s).",
            level=messages.SUCCESS,
        )


@admin.register(MessageDelivery)
class MessageDeliveryAdmin(admin.ModelAdmin):
    list_display = ("sent_at", "user", "message")
    list_filter = ("sent_at",)
    search_fields = ("user__username", "user__email", "message__subject", "message__body")
    ordering = ("-sent_at",)
    readonly_fields = ("message", "user", "sent_at")

