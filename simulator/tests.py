from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from competitions.models import Competition, CompetitionParticipant, CompetitionStatus, CompetitionType, ParticipantStatus
from marketdata.models import Instrument, Quote
from sponsors.models import Sponsor

from .models import Basket, BasketItem, Order, ScheduledBasketOrder, ScheduledBasketOrderStatus
from .services import execute_basket_order


class BasketTradingTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="u1", password="pw")
        self.sponsor = Sponsor.objects.create(name="S1")
        now = timezone.now()
        self.competition = Competition.objects.create(
            title="C1",
            sponsor=self.sponsor,
            week_start_at=now - timedelta(hours=1),
            week_end_at=now + timedelta(days=1),
            status=CompetitionStatus.PUBLISHED,
        )
        self.participant = CompetitionParticipant.objects.create(
            competition=self.competition,
            user=self.user,
            status=ParticipantStatus.ACTIVE,
            starting_cash=self.competition.starting_cash,
            cash_balance=Decimal("1000.00"),
        )

        self.aapl = Instrument.objects.create(symbol="AAPL", name="")
        self.ibm = Instrument.objects.create(symbol="IBM", name="")

        self.q_aapl = Quote.objects.create(
            instrument=self.aapl,
            as_of=now,
            price=Decimal("100.00"),
            provider_name="TEST",
        )
        self.q_ibm = Quote.objects.create(
            instrument=self.ibm,
            as_of=now,
            price=Decimal("50.00"),
            provider_name="TEST",
        )

    def _quote_side_effect(self, *, instrument):
        if instrument.symbol == "AAPL":
            return self.q_aapl
        if instrument.symbol == "IBM":
            return self.q_ibm
        return None

    @patch("simulator.services.fetch_and_store_latest_quote")
    def test_allocations_must_sum_to_100(self, mock_fetch):
        mock_fetch.side_effect = self._quote_side_effect
        result = execute_basket_order(
            participant_id=self.participant.id,
            basket_name="B",
            side="BUY",
            total_amount=Decimal("100.00"),
            pct_by_instrument_id={self.aapl.id: Decimal("60"), self.ibm.id: Decimal("30")},
        )
        self.assertFalse(result.ok)
        self.assertIn("total 100%", result.message)

    @patch("simulator.services.fetch_and_store_latest_quote")
    def test_allocations_cannot_include_zero(self, mock_fetch):
        mock_fetch.side_effect = self._quote_side_effect
        result = execute_basket_order(
            participant_id=self.participant.id,
            basket_name="B",
            side="BUY",
            total_amount=Decimal("100.00"),
            pct_by_instrument_id={self.aapl.id: Decimal("100"), self.ibm.id: Decimal("0")},
        )
        self.assertFalse(result.ok)
        self.assertIn(">", result.message)

    @patch("simulator.services.fetch_and_store_latest_quote")
    def test_per_symbol_max_pct_enforced(self, mock_fetch):
        mock_fetch.side_effect = self._quote_side_effect
        self.competition.competition_type = CompetitionType.ADVANCED
        self.competition.max_single_symbol_pct = Decimal("0.20")
        self.competition.save(update_fields=["competition_type", "max_single_symbol_pct", "updated_at"])

        result = execute_basket_order(
            participant_id=self.participant.id,
            basket_name="B",
            side="BUY",
            total_amount=Decimal("100.00"),
            pct_by_instrument_id={self.aapl.id: Decimal("50"), self.ibm.id: Decimal("50")},
        )
        self.assertFalse(result.ok)
        self.assertEqual((result.meta or {}).get("reason"), "ALLOCATION_OVER_MAX_PCT")

    @patch("simulator.services.fetch_and_store_latest_quote")
    def test_insufficient_cash_returns_meta(self, mock_fetch):
        mock_fetch.side_effect = self._quote_side_effect
        result = execute_basket_order(
            participant_id=self.participant.id,
            basket_name="B",
            side="BUY",
            total_amount=Decimal("5000.00"),
            pct_by_instrument_id={self.aapl.id: Decimal("50"), self.ibm.id: Decimal("50")},
        )
        self.assertFalse(result.ok)
        self.assertEqual((result.meta or {}).get("reason"), "INSUFFICIENT_CASH")
        self.assertIn("over", result.meta or {})

    @patch("simulator.services.fetch_and_store_latest_quote")
    def test_quote_refresh_failure_rejects(self, mock_fetch):
        def _side_effect(*, instrument):
            if instrument.symbol == "AAPL":
                return None
            return self.q_ibm

        mock_fetch.side_effect = _side_effect
        result = execute_basket_order(
            participant_id=self.participant.id,
            basket_name="B",
            side="BUY",
            total_amount=Decimal("100.00"),
            pct_by_instrument_id={self.aapl.id: Decimal("50"), self.ibm.id: Decimal("50")},
        )
        self.assertFalse(result.ok)
        self.assertEqual((result.meta or {}).get("reason"), "QUOTE_REFRESH_FAILED")

    @patch("simulator.services.fetch_and_store_latest_quote")
    def test_allocation_too_small_to_buy_one_share(self, mock_fetch):
        now = timezone.now()
        expensive = Instrument.objects.create(symbol="EXP", name="")
        q_exp = Quote.objects.create(
            instrument=expensive,
            as_of=now,
            price=Decimal("100000.00"),
            provider_name="TEST",
        )

        def _side_effect(*, instrument):
            if instrument.symbol == "EXP":
                return q_exp
            return self.q_ibm

        mock_fetch.side_effect = _side_effect
        result = execute_basket_order(
            participant_id=self.participant.id,
            basket_name="B",
            side="BUY",
            total_amount=Decimal("100.00"),
            pct_by_instrument_id={expensive.id: Decimal("50"), self.ibm.id: Decimal("50")},
        )
        self.assertFalse(result.ok)
        self.assertEqual((result.meta or {}).get("reason"), "ALLOCATION_TOO_SMALL")


class ScheduledBasketOrderTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="u1", password="pw")
        self.client = Client()
        self.client.login(username="u1", password="pw")

        self.sponsor = Sponsor.objects.create(name="S1")
        now = timezone.now()

        self.future_comp = Competition.objects.create(
            title="FUT",
            sponsor=self.sponsor,
            week_start_at=now + timedelta(hours=2),
            week_end_at=now + timedelta(days=1),
            status=CompetitionStatus.PUBLISHED,
        )
        self.future_participant = CompetitionParticipant.objects.create(
            competition=self.future_comp,
            user=self.user,
            status=ParticipantStatus.ACTIVE,
            starting_cash=Decimal("1000.00"),
            cash_balance=Decimal("1000.00"),
        )

        self.aapl = Instrument.objects.create(symbol="AAPL", name="")
        self.ibm = Instrument.objects.create(symbol="IBM", name="")

        self.basket = Basket.objects.create(user=self.user, name="My Basket", category="", notes="")
        BasketItem.objects.create(basket=self.basket, instrument=self.aapl)
        BasketItem.objects.create(basket=self.basket, instrument=self.ibm)

    def test_prestart_basket_order_is_saved(self):
        url = reverse("simulator:dashboard_for_competition", args=[self.future_comp.id])
        resp = self.client.post(
            url,
            data={
                "action": "basket_trade",
                "basket_id": str(self.basket.id),
                "basket_side": "BUY",
                "basket_total_amount": "100.00",
                f"pct_{self.aapl.id}": "50",
                f"pct_{self.ibm.id}": "50",
            },
            follow=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            ScheduledBasketOrder.objects.filter(
                participant=self.future_participant, status=ScheduledBasketOrderStatus.PENDING
            ).exists()
        )
        sbo = ScheduledBasketOrder.objects.get(participant=self.future_participant)
        self.assertEqual(sbo.total_amount, Decimal("100.00"))
        self.assertEqual(sbo.legs.count(), 2)
        self.assertEqual(Order.objects.count(), 0)

    def test_prestart_buy_validates_against_starting_cash(self):
        url = reverse("simulator:dashboard_for_competition", args=[self.future_comp.id])
        resp = self.client.post(
            url,
            data={
                "action": "basket_trade",
                "basket_id": str(self.basket.id),
                "basket_side": "BUY",
                "basket_total_amount": "2000.00",
                f"pct_{self.aapl.id}": "50",
                f"pct_{self.ibm.id}": "50",
            },
            follow=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(ScheduledBasketOrder.objects.count(), 0)

    @patch("simulator.services.fetch_and_store_latest_quote")
    def test_executor_command_executes_pending_orders(self, mock_fetch):
        now = timezone.now()
        comp = Competition.objects.create(
            title="ACT",
            sponsor=self.sponsor,
            week_start_at=now - timedelta(minutes=5),
            week_end_at=now + timedelta(days=1),
            status=CompetitionStatus.PUBLISHED,
        )
        participant = CompetitionParticipant.objects.create(
            competition=comp,
            user=self.user,
            status=ParticipantStatus.ACTIVE,
            starting_cash=Decimal("1000.00"),
            cash_balance=Decimal("1000.00"),
        )

        q_aapl = Quote.objects.create(
            instrument=self.aapl,
            as_of=now,
            price=Decimal("100.00"),
            provider_name="TEST",
        )
        q_ibm = Quote.objects.create(
            instrument=self.ibm,
            as_of=now,
            price=Decimal("50.00"),
            provider_name="TEST",
        )

        def _side_effect(*, instrument):
            if instrument.symbol == "AAPL":
                return q_aapl
            if instrument.symbol == "IBM":
                return q_ibm
            return None

        mock_fetch.side_effect = _side_effect

        sbo = ScheduledBasketOrder.objects.create(
            participant=participant,
            side="BUY",
            total_amount=Decimal("100.00"),
            basket_name="My Basket",
        )
        sbo.legs.create(instrument=self.aapl, pct=Decimal("50.00"))
        sbo.legs.create(instrument=self.ibm, pct=Decimal("50.00"))

        call_command("execute_scheduled_basket_orders")

        sbo.refresh_from_db()
        self.assertEqual(sbo.status, ScheduledBasketOrderStatus.EXECUTED)
        self.assertEqual(Order.objects.filter(participant=participant).count(), 2)
