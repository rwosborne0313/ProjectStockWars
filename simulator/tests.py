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

from .models import (
    Basket,
    BasketItem,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    ScheduledBasketOrder,
    ScheduledBasketOrderStatus,
)
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

    @patch("simulator.services.fetch_and_store_latest_quote")
    def test_executor_command_can_execute_future_orders_when_include_future(self, mock_fetch):
        now = timezone.now()
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
            participant=self.future_participant,
            side="BUY",
            total_amount=Decimal("100.00"),
            basket_name="My Basket",
        )
        sbo.legs.create(instrument=self.aapl, pct=Decimal("50.00"))
        sbo.legs.create(instrument=self.ibm, pct=Decimal("50.00"))

        call_command("execute_scheduled_basket_orders", "--include-future")

        sbo.refresh_from_db()
        self.assertEqual(sbo.status, ScheduledBasketOrderStatus.EXECUTED)
        self.assertEqual(Order.objects.filter(participant=self.future_participant).count(), 2)


class RecentOrdersPendingDisplayTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="u2", password="pw")
        self.client = Client()
        self.client.login(username="u2", password="pw")

        self.sponsor = Sponsor.objects.create(name="S2")
        now = timezone.now()
        self.future_comp = Competition.objects.create(
            title="FUT2",
            sponsor=self.sponsor,
            week_start_at=now + timedelta(hours=2),
            week_end_at=now + timedelta(days=1),
            status=CompetitionStatus.PUBLISHED,
        )
        self.participant = CompetitionParticipant.objects.create(
            competition=self.future_comp,
            user=self.user,
            status=ParticipantStatus.ACTIVE,
            starting_cash=Decimal("1000.00"),
            cash_balance=Decimal("1000.00"),
        )

        self.aapl = Instrument.objects.create(symbol="AAPL", name="")
        self.ibm = Instrument.objects.create(symbol="IBM", name="")
        Quote.objects.create(
            instrument=self.aapl,
            as_of=now,
            price=Decimal("100.00"),
            provider_name="TEST",
        )
        Quote.objects.create(
            instrument=self.ibm,
            as_of=now,
            price=Decimal("50.00"),
            provider_name="TEST",
        )

        self.basket = Basket.objects.create(user=self.user, name="My Basket", category="", notes="")
        BasketItem.objects.create(basket=self.basket, instrument=self.aapl)
        BasketItem.objects.create(basket=self.basket, instrument=self.ibm)

    def test_prestart_single_trade_is_queued_submitted(self):
        url = reverse("simulator:dashboard_for_competition", args=[self.future_comp.id])
        resp = self.client.post(
            url,
            data={
                "side": "BUY",
                "order_type": "LIMIT",
                "symbol": "AAPL",
                "quantity": "1",
                "limit_price": "101.00",
            },
            follow=False,
        )
        self.assertEqual(resp.status_code, 302)
        order = Order.objects.get(participant=self.participant)
        self.assertEqual(order.status, OrderStatus.SUBMITTED)
        self.assertEqual(order.reject_reason, "QUEUED_PRESTART")

    def test_recent_orders_shows_pending_basket_summary_and_legs_and_filters(self):
        url = reverse("simulator:dashboard_for_competition", args=[self.future_comp.id])
        self.client.post(
            url,
            data={
                "side": "BUY",
                "order_type": "LIMIT",
                "symbol": "AAPL",
                "quantity": "1",
                "limit_price": "101.00",
            },
            follow=False,
        )
        self.client.post(
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

        resp = self.client.get(url)
        self.assertContains(resp, "BASKET")
        self.assertContains(resp, "BASKET_LEG")
        self.assertContains(resp, "PENDING")
        self.assertContains(resp, "SUBMITTED")

        resp_pending = self.client.get(url, data={"status": "PENDING"})
        self.assertContains(resp_pending, "BASKET")
        self.assertNotContains(resp_pending, "SUBMITTED")

        resp_submitted = self.client.get(url, data={"status": "SUBMITTED"})
        self.assertContains(resp_submitted, "SUBMITTED")
        self.assertNotContains(resp_submitted, "PENDING")

        resp_basket_legs = self.client.get(url, data={"order_type": "BASKET_LEG", "symbol": "IBM"})
        self.assertContains(resp_basket_legs, "BASKET_LEG")
        self.assertContains(resp_basket_legs, "IBM")
        self.assertNotContains(resp_basket_legs, "SUBMITTED")

    def test_executor_command_processes_queued_single_order(self):
        now = timezone.now()
        active_comp = Competition.objects.create(
            title="ACT2",
            sponsor=self.sponsor,
            week_start_at=now - timedelta(minutes=5),
            week_end_at=now + timedelta(days=1),
            status=CompetitionStatus.PUBLISHED,
        )
        active_participant = CompetitionParticipant.objects.create(
            competition=active_comp,
            user=self.user,
            status=ParticipantStatus.ACTIVE,
            starting_cash=Decimal("1000.00"),
            cash_balance=Decimal("1000.00"),
        )
        Order.objects.create(
            participant=active_participant,
            instrument=self.aapl,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=2,
            limit_price=Decimal("200.00"),
            status=OrderStatus.SUBMITTED,
            reject_reason="QUEUED_PRESTART",
        )

        call_command("execute_scheduled_basket_orders")

        queued = Order.objects.get(participant=active_participant, instrument=self.aapl)
        self.assertEqual(queued.status, OrderStatus.FILLED)


class BasketOrderChangeLockTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="u3", password="pw")
        self.other_user = User.objects.create_user(username="u4", password="pw")
        self.client = Client()
        self.client.login(username="u3", password="pw")

        self.sponsor = Sponsor.objects.create(name="S3")
        self.aapl = Instrument.objects.create(symbol="AAPL", name="")
        self.ibm = Instrument.objects.create(symbol="IBM", name="")
        self.basket = Basket.objects.create(user=self.user, name="My Basket", category="", notes="")
        BasketItem.objects.create(basket=self.basket, instrument=self.aapl)
        BasketItem.objects.create(basket=self.basket, instrument=self.ibm)

    def _create_comp_participant(self, start_delta_minutes: int):
        now = timezone.now()
        comp = Competition.objects.create(
            title=f"C-{start_delta_minutes}",
            sponsor=self.sponsor,
            week_start_at=now + timedelta(minutes=start_delta_minutes),
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
        return comp, participant

    def test_cancel_pending_scheduled_basket_before_lock_window(self):
        comp, participant = self._create_comp_participant(start_delta_minutes=30)
        sbo = ScheduledBasketOrder.objects.create(
            participant=participant,
            side="BUY",
            total_amount=Decimal("100.00"),
            basket_name="My Basket",
        )
        sbo.legs.create(instrument=self.aapl, pct=Decimal("50.00"))
        sbo.legs.create(instrument=self.ibm, pct=Decimal("50.00"))

        url = reverse("simulator:dashboard_for_competition", args=[comp.id])
        resp = self.client.post(
            url,
            data={"action": "basket_cancel_scheduled", "scheduled_order_id": str(sbo.id)},
            follow=False,
        )
        self.assertEqual(resp.status_code, 302)
        sbo.refresh_from_db()
        self.assertEqual(sbo.status, ScheduledBasketOrderStatus.CANCELLED)

    def test_cancel_blocked_within_10_minute_lock_window(self):
        comp, participant = self._create_comp_participant(start_delta_minutes=9)
        sbo = ScheduledBasketOrder.objects.create(
            participant=participant,
            side="BUY",
            total_amount=Decimal("100.00"),
            basket_name="My Basket",
        )

        url = reverse("simulator:dashboard_for_competition", args=[comp.id])
        resp = self.client.post(
            url,
            data={"action": "basket_cancel_scheduled", "scheduled_order_id": str(sbo.id)},
            follow=False,
        )
        self.assertEqual(resp.status_code, 302)
        sbo.refresh_from_db()
        self.assertEqual(sbo.status, ScheduledBasketOrderStatus.PENDING)

    def test_schedule_blocked_within_10_minute_lock_window(self):
        comp, _participant = self._create_comp_participant(start_delta_minutes=10)
        url = reverse("simulator:dashboard_for_competition", args=[comp.id])

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
        self.assertEqual(ScheduledBasketOrder.objects.filter(participant__competition=comp).count(), 0)

    def test_cancelled_status_is_filterable_in_recent_orders(self):
        comp, participant = self._create_comp_participant(start_delta_minutes=30)
        sbo = ScheduledBasketOrder.objects.create(
            participant=participant,
            side="BUY",
            total_amount=Decimal("100.00"),
            basket_name="My Basket",
        )
        url = reverse("simulator:dashboard_for_competition", args=[comp.id])
        self.client.post(
            url,
            data={"action": "basket_cancel_scheduled", "scheduled_order_id": str(sbo.id)},
            follow=False,
        )
        resp = self.client.get(url, data={"status": "CANCELLED"})
        self.assertContains(resp, "CANCELLED")
        self.assertContains(resp, "BASKET")

    def test_pending_basket_row_renders_cancel_modal_trigger_with_details(self):
        comp, participant = self._create_comp_participant(start_delta_minutes=30)
        sbo = ScheduledBasketOrder.objects.create(
            participant=participant,
            side="BUY",
            total_amount=Decimal("123.45"),
            basket_name="Growth Mix",
        )
        sbo.legs.create(instrument=self.aapl, pct=Decimal("60.00"))
        sbo.legs.create(instrument=self.ibm, pct=Decimal("40.00"))

        url = reverse("simulator:dashboard_for_competition", args=[comp.id])
        resp = self.client.get(url)
        self.assertContains(resp, 'id="cancelScheduledBasketModal"')
        self.assertContains(resp, 'data-bs-target="#cancelScheduledBasketModal"')
        self.assertContains(resp, 'class="btn btn-sm btn-outline-danger js-cancel-scheduled-order"')
        self.assertContains(resp, f'data-order-id="{sbo.id}"')
        self.assertContains(resp, 'data-basket-name="Growth Mix"')
        self.assertContains(resp, 'data-total-amount="123.45"')
        self.assertContains(resp, 'data-leg-summary="AAPL 60.00%, IBM 40.00%"')

    def test_non_pending_basket_row_does_not_render_cancel_trigger(self):
        comp, participant = self._create_comp_participant(start_delta_minutes=30)
        sbo = ScheduledBasketOrder.objects.create(
            participant=participant,
            side="BUY",
            total_amount=Decimal("50.00"),
            basket_name="Executed Basket",
            status=ScheduledBasketOrderStatus.EXECUTED,
        )
        url = reverse("simulator:dashboard_for_competition", args=[comp.id])
        resp = self.client.get(url)
        self.assertContains(resp, "EXECUTED")
        self.assertNotContains(resp, f'data-order-id="{sbo.id}"')

    def test_non_owner_and_non_pending_orders_cannot_be_cancelled(self):
        comp, participant = self._create_comp_participant(start_delta_minutes=30)
        other_participant = CompetitionParticipant.objects.create(
            competition=comp,
            user=self.other_user,
            status=ParticipantStatus.ACTIVE,
            starting_cash=Decimal("1000.00"),
            cash_balance=Decimal("1000.00"),
        )
        other_sbo = ScheduledBasketOrder.objects.create(
            participant=other_participant,
            side="BUY",
            total_amount=Decimal("100.00"),
            basket_name="Other Basket",
        )
        executed_sbo = ScheduledBasketOrder.objects.create(
            participant=participant,
            side="BUY",
            total_amount=Decimal("50.00"),
            basket_name="Executed Basket",
            status=ScheduledBasketOrderStatus.EXECUTED,
        )
        url = reverse("simulator:dashboard_for_competition", args=[comp.id])

        self.client.post(
            url,
            data={"action": "basket_cancel_scheduled", "scheduled_order_id": str(other_sbo.id)},
            follow=False,
        )
        other_sbo.refresh_from_db()
        self.assertEqual(other_sbo.status, ScheduledBasketOrderStatus.PENDING)

        self.client.post(
            url,
            data={"action": "basket_cancel_scheduled", "scheduled_order_id": str(executed_sbo.id)},
            follow=False,
        )
        executed_sbo.refresh_from_db()
        self.assertEqual(executed_sbo.status, ScheduledBasketOrderStatus.EXECUTED)
