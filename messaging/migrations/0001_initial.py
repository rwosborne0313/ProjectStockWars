from __future__ import annotations

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AdminMessage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("subject", models.CharField(max_length=200)),
                ("body", models.TextField()),
                ("send_to_all", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="admin_messages_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "recipient",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="admin_messages_received",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["-created_at"], name="messaging_ad_created_bf1b56_idx"),
                    models.Index(fields=["-sent_at"], name="messaging_ad_sent_at_b3a857_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="MessageDelivery",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("sent_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "message",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="deliveries",
                        to="messaging.adminmessage",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="message_deliveries",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["user", "-sent_at"], name="messaging_me_user_id_2c1c0c_idx"),
                ],
            },
        ),
        migrations.AddConstraint(
            model_name="adminmessage",
            constraint=models.CheckConstraint(
                check=models.Q(
                    models.Q(("recipient__isnull", True), ("send_to_all", True)),
                    models.Q(("recipient__isnull", False), ("send_to_all", False)),
                    _connector="OR",
                ),
                name="adminmessage_send_to_all_xor_recipient",
            ),
        ),
        migrations.AddConstraint(
            model_name="messagedelivery",
            constraint=models.UniqueConstraint(
                fields=("message", "user"), name="uniq_message_delivery_message_user"
            ),
        ),
    ]

