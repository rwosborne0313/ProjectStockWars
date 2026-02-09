from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from competitions.models import Competition, CompetitionParticipant, CompetitionStatus, CompetitionType, ParticipantStatus
from simulator.models import CashLedgerEntry, CashLedgerReason
from sponsors.models import Sponsor


class AdvancedJoinQueueTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="u1", password="pw")
        self.client = Client()
        self.client.login(username="u1", password="pw")
        self.sponsor = Sponsor.objects.create(name="S1")

    def test_join_before_start_queues_participant(self):
        now = timezone.now()
        comp = Competition.objects.create(
            title="C",
            sponsor=self.sponsor,
            week_start_at=now + timedelta(hours=2),
            week_end_at=now + timedelta(days=1),
            status=CompetitionStatus.PUBLISHED,
            competition_type=CompetitionType.ADVANCED,
            disallow_join_after_start=True,
        )

        resp = self.client.get(reverse("competitions:join_competition", args=[comp.id]))
        self.assertEqual(resp.status_code, 302)

        p = CompetitionParticipant.objects.get(competition=comp, user=self.user)
        self.assertEqual(p.status, ParticipantStatus.QUEUED)
        self.assertEqual(Decimal(p.cash_balance), Decimal("0.00"))

    def test_join_after_start_is_blocked(self):
        now = timezone.now()
        comp = Competition.objects.create(
            title="C",
            sponsor=self.sponsor,
            week_start_at=now - timedelta(hours=1),
            week_end_at=now + timedelta(days=1),
            status=CompetitionStatus.PUBLISHED,
            competition_type=CompetitionType.ADVANCED,
            disallow_join_after_start=True,
        )

        resp = self.client.get(reverse("competitions:join_competition", args=[comp.id]))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(
            CompetitionParticipant.objects.filter(competition=comp, user=self.user).exists()
        )

    def test_activation_command_activates_and_credits_starting_cash(self):
        now = timezone.now()
        comp = Competition.objects.create(
            title="C",
            sponsor=self.sponsor,
            week_start_at=now - timedelta(minutes=1),
            week_end_at=now + timedelta(days=1),
            status=CompetitionStatus.PUBLISHED,
            competition_type=CompetitionType.ADVANCED,
            disallow_join_after_start=True,
        )
        p = CompetitionParticipant.objects.create(
            competition=comp,
            user=self.user,
            status=ParticipantStatus.QUEUED,
            starting_cash=comp.starting_cash,
            cash_balance=Decimal("0.00"),
        )

        call_command("activate_queued_participants")

        p.refresh_from_db()
        self.assertEqual(p.status, ParticipantStatus.ACTIVE)
        self.assertEqual(Decimal(p.cash_balance), Decimal(p.starting_cash))

        self.assertTrue(
            CashLedgerEntry.objects.filter(
                participant=p, reason=CashLedgerReason.STARTING_CASH, delta_amount=p.starting_cash
            ).exists()
        )
