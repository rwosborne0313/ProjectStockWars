from decimal import Decimal
from datetime import timezone as py_timezone, timedelta
from math import ceil
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db.models import Sum
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from competitions.models import CompetitionParticipant, CompetitionStatus, ParticipantStatus
from leaderboards.models import PortfolioSnapshot
from marketdata.models import Quote, Watchlist, WatchlistItem
from marketdata.providers import TwelveDataProvider
from marketdata.services import fetch_and_store_latest_quote, get_or_create_instrument_by_symbol, normalize_symbol

from .forms import (
    BasketAddSymbolForm,
    BasketCreateForm,
    BasketDeleteForm,
    BasketEditForm,
    BasketRemoveSymbolForm,
    OrderSearchForm,
    TradeTicketForm,
    WatchlistAddForm,
    WatchlistCreateForm,
    WatchlistDeleteForm,
    WatchlistRemoveForm,
)
from .models import (
    Basket,
    BasketItem,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    ScheduledBasketOrder,
    ScheduledBasketOrderStatus,
    ScheduledBasketOrderLeg,
    TradeFill,
)
from .services import execute_basket_order, execute_order


def _rank_desc(values_by_id: dict[int, Decimal], subject_id: int) -> tuple[int | None, int]:
    """
    Return (rank, total_count) where rank is 1 + number of participants with a strictly greater value.
    If subject_id is missing, rank is None.
    """
    total = len(values_by_id)
    if subject_id not in values_by_id:
        return None, total
    subject_value = values_by_id[subject_id]
    greater = 0
    for v in values_by_id.values():
        if v > subject_value:
            greater += 1
    return greater + 1, total

# Create your views here.


@login_required
def dashboard(request):
    """
    Convenience route: redirect to a specific competition dashboard if the user has joined
    any active competition. Otherwise send them to the active competitions list.
    """
    now = timezone.now()
    participant = (
        CompetitionParticipant.objects.filter(
            user=request.user,
            status=ParticipantStatus.ACTIVE,
            competition__status=CompetitionStatus.PUBLISHED,
            competition__week_start_at__lte=now,
            competition__week_end_at__gte=now,
        )
        .select_related("competition")
        .order_by("-joined_at")
        .first()
    )

    if not participant:
        messages.info(request, "Join an active competition to start trading.")
        return redirect("competitions:active_competitions")

    return redirect("simulator:dashboard_for_competition", competition_id=participant.competition_id)


@login_required
def dashboard_for_competition(request, competition_id: int):
    now = timezone.now()
    participant = (
        CompetitionParticipant.objects.filter(user=request.user, competition_id=competition_id)
        .select_related("competition")
        .first()
    )
    if not participant:
        messages.info(request, "Join this competition to access its dashboard.")
        return redirect("competitions:competition_detail", competition_id=competition_id)

    if request.method == "POST":
        def _basket_changes_locked() -> bool:
            seconds_until_start = (participant.competition.week_start_at - now).total_seconds()
            return seconds_until_start <= 600

        def _ensure_default_watchlist() -> Watchlist:
            wl = Watchlist.objects.filter(user=request.user).order_by("id").first()
            if wl:
                return wl
            return Watchlist.objects.create(user=request.user, name="My Watchlist", industry_label="")

        if request.POST.get("action") == "basket_cancel_scheduled":
            try:
                scheduled_order_id = int(request.POST.get("scheduled_order_id") or 0)
            except (TypeError, ValueError):
                scheduled_order_id = 0
            scheduled = ScheduledBasketOrder.objects.filter(
                id=scheduled_order_id,
                participant=participant,
            ).first()
            if not scheduled:
                messages.error(request, "Scheduled basket order not found.")
                return redirect("simulator:dashboard_for_competition", competition_id=competition_id)
            if scheduled.status != ScheduledBasketOrderStatus.PENDING:
                messages.error(request, "Only pending scheduled basket orders can be cancelled.")
                return redirect("simulator:dashboard_for_competition", competition_id=competition_id)
            if now >= participant.competition.week_start_at:
                messages.error(request, "Cannot cancel basket orders after the competition starts.")
                return redirect("simulator:dashboard_for_competition", competition_id=competition_id)
            if _basket_changes_locked():
                messages.error(
                    request,
                    "Basket order changes are locked in the last 10 minutes before competition start.",
                )
                return redirect("simulator:dashboard_for_competition", competition_id=competition_id)
            scheduled.status = ScheduledBasketOrderStatus.CANCELLED
            scheduled.last_error = "Cancelled by user before start."
            scheduled.save(update_fields=["status", "last_error"])
            messages.success(request, "Scheduled basket order cancelled.")
            return redirect("simulator:dashboard_for_competition", competition_id=competition_id)

        if request.POST.get("action") == "basket_trade":
            try:
                basket_id = int(request.POST.get("basket_id") or 0)
            except (TypeError, ValueError):
                basket_id = 0
            basket = (
                Basket.objects.filter(id=basket_id, user=request.user)
                .prefetch_related("items__instrument")
                .first()
            )
            if not basket:
                messages.error(request, "Basket not found.")
                return redirect("simulator:dashboard_for_competition", competition_id=competition_id)

            side = (request.POST.get("basket_side") or "").strip().upper()
            raw_total = (request.POST.get("basket_total_amount") or "").strip()
            try:
                total_amount = Decimal(raw_total)
            except Exception:
                messages.error(request, "Total amount is invalid.")
                return redirect("simulator:dashboard_for_competition", competition_id=competition_id)

            items = sorted(list(basket.items.all()), key=lambda x: (x.instrument.symbol, x.id))
            if not items:
                messages.error(request, "This basket has no symbols.")
                return redirect("simulator:dashboard_for_competition", competition_id=competition_id)

            pct_by_instrument_id: dict[int, Decimal] = {}
            for it in items:
                key = f"pct_{it.instrument_id}"
                raw = (request.POST.get(key) or "").strip()
                try:
                    pct_by_instrument_id[it.instrument_id] = Decimal(raw)
                except Exception:
                    pct_by_instrument_id[it.instrument_id] = Decimal("0")

            # If the competition hasn't started yet, save the basket order for execution at start.
            if now < participant.competition.week_start_at:
                if _basket_changes_locked():
                    messages.error(
                        request,
                        "Basket order changes are locked in the last 10 minutes before competition start.",
                    )
                    return redirect("simulator:dashboard_for_competition", competition_id=competition_id)
                competition = participant.competition
                max_pct = (
                    Decimal(competition.max_single_symbol_pct)
                    if competition.competition_type == "ADVANCED"
                    and competition.max_single_symbol_pct is not None
                    else Decimal("0.33")
                )

                # Validate allocations: each > 0, sum=100, and each <= max per symbol %.
                total_pct = Decimal("0.00")
                for it in items:
                    pct = pct_by_instrument_id.get(it.instrument_id) or Decimal("0")
                    if pct <= 0:
                        messages.error(request, f"Allocation for {it.instrument.symbol} must be > 0%.")
                        return redirect(
                            "simulator:dashboard_for_competition", competition_id=competition_id
                        )
                    if pct > (max_pct * Decimal("100")):
                        messages.error(
                            request,
                            f"{it.instrument.symbol} allocation exceeds the max per-symbol percent for this competition.",
                        )
                        return redirect(
                            "simulator:dashboard_for_competition", competition_id=competition_id
                        )
                    total_pct += pct

                if abs(total_pct - Decimal("100.00")) > Decimal("0.01"):
                    messages.error(request, "Allocations must total 100%.")
                    return redirect("simulator:dashboard_for_competition", competition_id=competition_id)

                # Buying power validation for pre-start scheduling (validate vs starting_cash).
                if side == OrderSide.BUY and total_amount > Decimal(participant.starting_cash):
                    over = total_amount - Decimal(participant.starting_cash)
                    messages.error(
                        request,
                        f"Total basket amount exceeds your starting cash by ${over:,.2f}. Reduce the total and try again.",
                    )
                    return redirect("simulator:dashboard_for_competition", competition_id=competition_id)

                scheduled = ScheduledBasketOrder.objects.create(
                    participant=participant,
                    side=side,
                    total_amount=total_amount,
                    basket_name=basket.name,
                )
                ScheduledBasketOrderLeg.objects.bulk_create(
                    [
                        ScheduledBasketOrderLeg(
                            order=scheduled, instrument_id=it.instrument_id, pct=pct_by_instrument_id[it.instrument_id]
                        )
                        for it in items
                    ]
                )
                messages.success(
                    request,
                    "Basket order scheduled for execution at competition start.",
                )
                return redirect("simulator:dashboard_for_competition", competition_id=competition_id)

            result = execute_basket_order(
                participant_id=participant.id,
                basket_name=basket.name,
                side=side,
                total_amount=total_amount,
                pct_by_instrument_id=pct_by_instrument_id,
            )
            if result.ok:
                messages.success(request, result.message)
            else:
                if (result.meta or {}).get("reason") == "INSUFFICIENT_CASH":
                    request.session["basket_cash_modal"] = result.meta
                    messages.error(request, result.message, extra_tags="basket-cash-modal")
                else:
                    messages.error(request, result.message)
            return redirect("simulator:dashboard_for_competition", competition_id=competition_id)

        if request.POST.get("action") == "positions_refresh":
            positions = list(
                participant.positions.filter(quantity__gt=0).select_related("instrument")
            )
            refreshed = 0
            failed = 0
            for pos in positions:
                q = fetch_and_store_latest_quote(instrument=pos.instrument)
                if q is None:
                    failed += 1
                else:
                    refreshed += 1
            if refreshed:
                messages.success(request, f"Refreshed {refreshed} position quote(s).")
            if failed:
                messages.warning(request, f"Failed to refresh {failed} symbol(s). Try again soon.")
            return redirect("simulator:dashboard_for_competition", competition_id=competition_id)

        if request.POST.get("action") == "close_position":
            instrument_id = int(request.POST.get("instrument_id") or 0)
            pos = participant.positions.filter(instrument_id=instrument_id).select_related("instrument").first()
            if not pos or pos.quantity <= 0:
                messages.error(request, "Position not found.")
                return redirect("simulator:dashboard_for_competition", competition_id=competition_id)
            result = execute_order(
                participant_id=participant.id,
                instrument_id=instrument_id,
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                quantity=pos.quantity,
            )
            messages.success(request, result.message) if result.ok else messages.error(request, result.message)
            return redirect("simulator:dashboard_for_competition", competition_id=competition_id)

        if request.POST.get("action") == "watchlist_add":
            watchlist_add_form = WatchlistAddForm(request.POST, participant=participant)
            if watchlist_add_form.is_valid():
                default_watchlist = _ensure_default_watchlist()
                instrument = get_or_create_instrument_by_symbol(watchlist_add_form.cleaned_data["symbol"])
                WatchlistItem.objects.get_or_create(watchlist=default_watchlist, instrument=instrument)
                # Immediately refresh quote so the watchlist shows a price on the next render.
                try:
                    fetch_and_store_latest_quote(instrument=instrument)
                except Exception:
                    # If the provider is down / rate-limited, keep the watchlist item but show a hint.
                    messages.warning(request, f"Added {instrument.symbol}, but could not refresh quote right now.")
                messages.success(request, f"Added {instrument.symbol} to watchlist.")
            else:
                messages.error(request, "Could not add to watchlist.")
            return redirect("simulator:dashboard_for_competition", competition_id=competition_id)

        if request.POST.get("action") == "watchlist_refresh":
            default_watchlist = _ensure_default_watchlist()
            watchlist_items = list(
                WatchlistItem.objects.filter(watchlist=default_watchlist).select_related("instrument")
            )
            refreshed = 0
            failed = 0
            for item in watchlist_items:
                q = fetch_and_store_latest_quote(instrument=item.instrument)
                if q is None:
                    failed += 1
                else:
                    refreshed += 1
            if refreshed:
                messages.success(request, f"Refreshed {refreshed} watchlist quote(s).")
            if failed:
                messages.warning(request, f"Failed to refresh {failed} symbol(s). Try again soon.")
            return redirect("simulator:dashboard_for_competition", competition_id=competition_id)

        if request.POST.get("action") == "watchlist_remove":
            default_watchlist = _ensure_default_watchlist()
            watchlist_remove_form = WatchlistRemoveForm(request.POST)
            if watchlist_remove_form.is_valid():
                instrument_id = watchlist_remove_form.cleaned_data["instrument_id"]
                WatchlistItem.objects.filter(watchlist=default_watchlist, instrument_id=instrument_id).delete()
                messages.success(request, "Removed from watchlist.")
            else:
                messages.error(request, "Could not remove from watchlist.")
            return redirect("simulator:dashboard_for_competition", competition_id=competition_id)

        form = TradeTicketForm(request.POST, participant=participant)
        if form.is_valid():
            instrument = get_or_create_instrument_by_symbol(form.cleaned_data["symbol"])
            if now < participant.competition.week_start_at:
                latest_quote = (
                    Quote.objects.filter(instrument_id=instrument.id)
                    .order_by("-as_of")
                    .only("price", "as_of")
                    .first()
                )
                Order.objects.create(
                    participant=participant,
                    instrument=instrument,
                    side=form.cleaned_data["side"],
                    order_type=form.cleaned_data["order_type"],
                    quantity=form.cleaned_data["quantity"],
                    limit_price=form.cleaned_data.get("limit_price"),
                    status=OrderStatus.SUBMITTED,
                    submitted_price=(latest_quote.price if latest_quote else None),
                    quote_as_of=(latest_quote.as_of if latest_quote else None),
                    reject_reason="QUEUED_PRESTART",
                )
                messages.success(request, "Order queued for execution at competition start.")
                return redirect("simulator:dashboard_for_competition", competition_id=competition_id)
            result = execute_order(
                participant_id=participant.id,
                instrument_id=instrument.id,
                side=form.cleaned_data["side"],
                order_type=form.cleaned_data["order_type"],
                quantity=form.cleaned_data["quantity"],
                limit_price=form.cleaned_data.get("limit_price"),
            )
            if result.ok:
                messages.success(request, result.message)
            else:
                if getattr(result.order, "reject_reason", "") in {
                    "POSITION_SIZE_LIMIT_33PCT",
                    "POSITION_SIZE_LIMIT_MAX_PCT",
                }:
                    # Persist details across redirect so the dashboard modal can render a breakdown.
                    if result.meta:
                        request.session["trade_limit_modal"] = result.meta
                    messages.error(request, result.message, extra_tags="trade-limit-modal")
                else:
                    messages.error(request, result.message)
            return redirect("simulator:dashboard_for_competition", competition_id=competition_id)
    else:
        form = TradeTicketForm(participant=participant)

    positions = list(
        participant.positions.filter(quantity__gt=0)
        .select_related("instrument")
        .order_by("instrument__symbol")
    )

    # Pull latest quotes for held instruments (naive approach; optimize later if needed)
    latest_quotes: dict[int, Quote] = {}
    for pos in positions:
        q = (
            Quote.objects.filter(instrument=pos.instrument)
            .order_by("-as_of")
            .only("instrument_id", "as_of", "price")
            .first()
        )
        if q:
            latest_quotes[pos.instrument_id] = q

    holdings_value = Decimal("0.00")
    position_rows = []
    for pos in positions:
        q = latest_quotes.get(pos.instrument_id)
        last_price = q.price if q else None
        market_value = (last_price * Decimal(pos.quantity)) if last_price is not None else None
        if market_value is not None:
            holdings_value += market_value
        unrealized = (
            (last_price - pos.avg_cost_basis) * Decimal(pos.quantity)
            if last_price is not None and pos.quantity
            else None
        )
        unrealized_pct = (
            (last_price - pos.avg_cost_basis) / pos.avg_cost_basis
            if last_price is not None and pos.avg_cost_basis and pos.quantity
            else None
        )
        position_rows.append(
            {
                "instrument_id": pos.instrument_id,
                "symbol": pos.instrument.symbol,
                "quantity": pos.quantity,
                "avg_cost": pos.avg_cost_basis,
                "last_price": last_price,
                "quote_as_of": q.as_of if q else None,
                "market_value": market_value,
                "unrealized": unrealized,
                "unrealized_pct": unrealized_pct,
            }
        )

    cash_balance = participant.cash_balance
    total_value = cash_balance + holdings_value
    return_pct = (
        (total_value - participant.starting_cash) / participant.starting_cash
        if participant.starting_cash
        else Decimal("0")
    )

    recent_orders: list[dict] = []
    order_rows = (
        Order.objects.filter(participant=participant)
        .select_related("instrument")
        .prefetch_related("fills")
    )
    for o in order_rows:
        recent_orders.append(
            {
                "created_at": o.created_at,
                "scheduled_order_id": None,
                "basket_name": None,
                "total_amount": None,
                "basket_leg_summary": "",
                "symbol": o.instrument.symbol,
                "order_type": o.order_type,
                "side": o.side,
                "quantity": o.quantity,
                "limit_price": o.limit_price,
                "status": o.status,
                "submitted_price": o.submitted_price,
                "fill_prices": [f.price for f in o.fills.all()],
            }
        )

    scheduled_rows = (
        ScheduledBasketOrder.objects.filter(participant=participant)
        .prefetch_related("legs__instrument")
        .order_by("-created_at")
    )
    for sbo in scheduled_rows:
        leg_summary = ", ".join(
            f"{leg.instrument.symbol} {leg.pct:.2f}%"
            for leg in sorted(sbo.legs.all(), key=lambda l: l.instrument.symbol)
        )
        recent_orders.append(
            {
                "created_at": sbo.created_at,
                "scheduled_order_id": sbo.id,
                "basket_name": sbo.basket_name,
                "total_amount": sbo.total_amount,
                "basket_leg_summary": leg_summary,
                "symbol": "BASKET",
                "order_type": "BASKET",
                "side": sbo.side,
                "quantity": None,
                "limit_price": None,
                "status": sbo.status,
                "submitted_price": None,
                "fill_prices": [],
            }
        )
        for leg in sbo.legs.all():
            recent_orders.append(
                {
                    "created_at": sbo.created_at,
                    "scheduled_order_id": sbo.id,
                    "basket_name": sbo.basket_name,
                    "total_amount": sbo.total_amount,
                    "basket_leg_summary": leg_summary,
                    "symbol": leg.instrument.symbol,
                    "order_type": "BASKET_LEG",
                    "side": sbo.side,
                    "quantity": None,
                    "limit_price": None,
                    "status": sbo.status,
                    "submitted_price": None,
                    "fill_prices": [],
                }
            )

    order_search_form = OrderSearchForm(request.GET or None)
    if order_search_form.is_valid():
        cd = order_search_form.cleaned_data

        def _price_match(row: dict, target: Decimal) -> bool:
            if row.get("submitted_price") == target or row.get("limit_price") == target:
                return True
            for p in row.get("fill_prices") or []:
                if p == target:
                    return True
            return False

        filtered_rows = []
        placed_date = cd.get("placed_date")
        symbol = (cd.get("symbol") or "").upper()
        for row in recent_orders:
            if placed_date:
                if timezone.localtime(row["created_at"]).date() != placed_date:
                    continue
            if symbol and row["symbol"].upper() != symbol:
                continue
            if cd.get("order_type") and row["order_type"] != cd["order_type"]:
                continue
            if cd.get("side") and row["side"] != cd["side"]:
                continue
            if cd.get("quantity") and row.get("quantity") != cd["quantity"]:
                continue
            if cd.get("status") and row["status"] != cd["status"]:
                continue
            if cd.get("price") is not None and not _price_match(row, cd["price"]):
                continue
            filtered_rows.append(row)
        recent_orders = filtered_rows

    recent_orders = sorted(recent_orders, key=lambda r: r["created_at"], reverse=True)
    recent_orders = recent_orders[:200] if order_search_form.is_valid() else recent_orders[:50]

    # Realized P&L: sum realized_pnl of SELL fills
    realized_total = (
        TradeFill.objects.filter(order__participant=participant, order__side=OrderSide.SELL)
        .aggregate(total=Sum("realized_pnl"))["total"]
        or Decimal("0.00")
    )
    local_now = timezone.localtime(now)
    start_of_day = local_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(py_timezone.utc)
    realized_today = (
        TradeFill.objects.filter(
            order__participant=participant,
            order__side=OrderSide.SELL,
            filled_at__gte=start_of_day,
        ).aggregate(total=Sum("realized_pnl"))["total"]
        or Decimal("0.00")
    )
    unrealized_total = Decimal("0.00")
    for row in position_rows:
        if row["unrealized"] is not None:
            unrealized_total += row["unrealized"]

    # Fill % portfolio after we know total_value
    for row in position_rows:
        row["pct_portfolio"] = (
            (row["market_value"] / total_value) if row["market_value"] is not None and total_value else None
        )

    # Watchlist
    default_watchlist = Watchlist.objects.filter(user=request.user).order_by("id").first()
    watchlist_items = list(
        WatchlistItem.objects.filter(watchlist=default_watchlist) if default_watchlist else WatchlistItem.objects.none()
        .select_related("instrument")
        .order_by("instrument__symbol")
    )
    watchlist_rows = []
    for item in watchlist_items:
        q = (
            Quote.objects.filter(instrument=item.instrument)
            .order_by("-as_of")
            .only("instrument_id", "as_of", "price")
            .first()
        )
        watchlist_rows.append(
            {
                "instrument_id": item.instrument_id,
                "symbol": item.instrument.symbol,
                "last_price": q.price if q else None,
                "quote_as_of": q.as_of if q else None,
            }
        )

    watchlist_add_form = WatchlistAddForm(
        participant=participant,
        initial={"watchlist_id": default_watchlist.id} if default_watchlist else None,
    )

    joined_participants = (
        CompetitionParticipant.objects.filter(user=request.user)
        .select_related("competition")
        .order_by("-joined_at")
    )

    # --- Rankings (no other-investor details exposed) ---
    comp_participants = list(
        CompetitionParticipant.objects.filter(
            competition_id=competition_id,
            status=ParticipantStatus.ACTIVE,
            competition__status=CompetitionStatus.PUBLISHED,
        ).select_related("competition")
    )
    participant_ids = [p.id for p in comp_participants]

    values_cash: dict[int, Decimal] = {p.id: p.cash_balance for p in comp_participants}

    # Positions valuation across all participants
    from simulator.models import Position

    pos_rows = list(
        Position.objects.filter(participant_id__in=participant_ids, quantity__gt=0).values(
            "participant_id", "instrument_id", "quantity", "avg_cost_basis"
        )
    )
    instrument_ids = sorted({r["instrument_id"] for r in pos_rows})
    latest_prices: dict[int, Decimal] = {}
    if instrument_ids:
        latest_quotes = (
            Quote.objects.filter(instrument_id__in=instrument_ids)
            .order_by("instrument_id", "-as_of")
            .distinct("instrument_id")
            .only("instrument_id", "price")
        )
        latest_prices = {q.instrument_id: q.price for q in latest_quotes}

    values_holdings: dict[int, Decimal] = {pid: Decimal("0.00") for pid in participant_ids}
    values_unrealized: dict[int, Decimal] = {pid: Decimal("0.00") for pid in participant_ids}
    for r in pos_rows:
        price = latest_prices.get(r["instrument_id"])
        if price is None:
            continue
        qty = Decimal(r["quantity"])
        avg_cost = r["avg_cost_basis"]
        pid = r["participant_id"]
        values_holdings[pid] = values_holdings.get(pid, Decimal("0.00")) + (price * qty)
        values_unrealized[pid] = values_unrealized.get(pid, Decimal("0.00")) + ((price - avg_cost) * qty)

    values_equity: dict[int, Decimal] = {
        pid: values_cash.get(pid, Decimal("0.00")) + values_holdings.get(pid, Decimal("0.00"))
        for pid in participant_ids
    }

    # Realized P&L totals across participants
    realized_totals = {
        row["order__participant_id"]: row["total"] or Decimal("0.00")
        for row in TradeFill.objects.filter(
            order__participant_id__in=participant_ids,
            order__side=OrderSide.SELL,
        )
        .values("order__participant_id")
        .annotate(total=Sum("realized_pnl"))
    }
    realized_todays = {
        row["order__participant_id"]: row["total"] or Decimal("0.00")
        for row in TradeFill.objects.filter(
            order__participant_id__in=participant_ids,
            order__side=OrderSide.SELL,
            filled_at__gte=start_of_day,
        )
        .values("order__participant_id")
        .annotate(total=Sum("realized_pnl"))
    }
    values_realized_total: dict[int, Decimal] = {
        pid: realized_totals.get(pid, Decimal("0.00")) for pid in participant_ids
    }
    values_realized_today: dict[int, Decimal] = {
        pid: realized_todays.get(pid, Decimal("0.00")) for pid in participant_ids
    }

    rank_equity, total_in_comp = _rank_desc(values_equity, participant.id)
    rank_cash, _ = _rank_desc(values_cash, participant.id)
    rank_holdings, _ = _rank_desc(values_holdings, participant.id)
    rank_unrealized, _ = _rank_desc(values_unrealized, participant.id)
    rank_realized_today, _ = _rank_desc(values_realized_today, participant.id)
    rank_realized_total, _ = _rank_desc(values_realized_total, participant.id)

    ranking_card = {
        "total": total_in_comp,
        "equity": rank_equity,
        "cash": rank_cash,
        "holdings": rank_holdings,
        "unrealized": rank_unrealized,
        "realized_today": rank_realized_today,
        "realized_total": rank_realized_total,
    }

    # Competition-wide rankings table (anonymous, paginated)
    ranked_participant_ids = sorted(
        participant_ids,
        key=lambda pid: (values_equity.get(pid, Decimal("0.00")), pid),
        reverse=True,
    )
    competition_rows = []
    for idx, pid in enumerate(ranked_participant_ids, start=1):
        competition_rows.append(
            {
                "rank": idx,
                "label": "You" if pid == participant.id else f"Trader #{idx}",
                "is_me": pid == participant.id,
                "equity": values_equity.get(pid, Decimal("0.00")),
                "cash": values_cash.get(pid, Decimal("0.00")),
                "holdings": values_holdings.get(pid, Decimal("0.00")),
                "unrealized": values_unrealized.get(pid, Decimal("0.00")),
                "realized_today": values_realized_today.get(pid, Decimal("0.00")),
                "realized_total": values_realized_total.get(pid, Decimal("0.00")),
            }
        )

    try:
        rank_page = int(request.GET.get("rank_page") or 1)
    except (TypeError, ValueError):
        rank_page = 1
    rank_page_size = 50
    competition_table_total = len(competition_rows)
    competition_table_pages = max(1, int(ceil(competition_table_total / rank_page_size))) if competition_table_total else 1
    rank_page = max(1, min(rank_page, competition_table_pages))
    start_idx = (rank_page - 1) * rank_page_size
    end_idx = min(start_idx + rank_page_size, competition_table_total)
    competition_table_rows = competition_rows[start_idx:end_idx]

    def _page_url(page_num: int) -> str:
        q = request.GET.copy()
        q["rank_page"] = str(page_num)
        return f"{request.path}?{urlencode(q, doseq=True)}"

    competition_table_prev_url = _page_url(rank_page - 1) if rank_page > 1 else None
    competition_table_next_url = _page_url(rank_page + 1) if rank_page < competition_table_pages else None

    # Compact page number list (max 7 links)
    page_links = []
    if competition_table_pages <= 7:
        page_links = list(range(1, competition_table_pages + 1))
    else:
        # Center around current page
        left = max(1, rank_page - 2)
        right = min(competition_table_pages, rank_page + 2)
        if left <= 2:
            left, right = 1, 5
        elif right >= competition_table_pages - 1:
            left, right = competition_table_pages - 4, competition_table_pages
        page_links = [1]
        if left > 2:
            page_links.append(None)
        page_links.extend(list(range(left, right + 1)))
        if right < competition_table_pages - 1:
            page_links.append(None)
        page_links.append(competition_table_pages)

    competition_table_page_link_objs = []
    for p in page_links:
        if p is None:
            competition_table_page_link_objs.append({"gap": True})
        else:
            competition_table_page_link_objs.append(
                {"gap": False, "page": p, "url": _page_url(p), "is_current": p == rank_page}
            )

    trade_limit_modal = request.session.pop("trade_limit_modal", None)
    basket_cash_modal = request.session.pop("basket_cash_modal", None)
    competition_end_at = participant.competition.week_end_at
    competition_is_over = now >= competition_end_at
    competition_end_iso = timezone.localtime(competition_end_at).isoformat()

    # Baskets (user-owned symbol groups used by the basket-trade modal)
    user_baskets = list(
        Basket.objects.filter(user=request.user)
        .prefetch_related("items__instrument")
        .order_by("-updated_at", "name", "id")
    )
    basket_map = {
        b.id: {
            "id": b.id,
            "name": b.name,
            "symbols": [
                {"instrument_id": bi.instrument_id, "symbol": bi.instrument.symbol}
                for bi in sorted(b.items.all(), key=lambda x: (x.instrument.symbol, x.id))
            ],
        }
        for b in user_baskets
    }
    max_pct = (
        participant.competition.max_single_symbol_pct
        if participant.competition.max_single_symbol_pct is not None
        else Decimal("0.33")
    )

    return render(
        request,
        "simulator/dashboard.html",
        {
            "participant": participant,
            "joined_participants": joined_participants,
            "form": form,
            "order_search_form": order_search_form,
            "position_rows": position_rows,
            "cash_balance": cash_balance,
            "holdings_value": holdings_value,
            "total_value": total_value,
            "return_pct": return_pct,
            "recent_orders": recent_orders,
            "unrealized_total": unrealized_total,
            "realized_total": realized_total,
            "realized_today": realized_today,
            "watchlist_rows": watchlist_rows,
            "watchlist_add_form": watchlist_add_form,
            "ranking_card": ranking_card,
            "competition_table_rows": competition_table_rows,
            "competition_table_page": rank_page,
            "competition_table_pages": competition_table_pages,
            "competition_table_total": competition_table_total,
            "competition_table_start": start_idx + 1 if competition_table_total else 0,
            "competition_table_end": end_idx if competition_table_total else 0,
            "competition_table_prev_url": competition_table_prev_url,
            "competition_table_next_url": competition_table_next_url,
            "competition_table_page_links": competition_table_page_link_objs,
            "trade_limit_modal": trade_limit_modal,
            "basket_cash_modal": basket_cash_modal,
            "competition_end_iso": competition_end_iso,
            "competition_is_over": competition_is_over,
            "user_baskets": user_baskets,
            "basket_map": basket_map,
            "basket_max_pct": max_pct,
        },
    )


@login_required
def watchlist(request):
    def _ensure_default_watchlist() -> Watchlist:
        wl = Watchlist.objects.filter(user=request.user).order_by("id").first()
        if wl:
            return wl
        return Watchlist.objects.create(user=request.user, name="My Watchlist", industry_label="")

    def _get_active_watchlist() -> Watchlist:
        try:
            wid = int(request.GET.get("watchlist_id") or 0)
        except (TypeError, ValueError):
            wid = 0
        if wid:
            wl = Watchlist.objects.filter(id=wid, user=request.user).first()
            if wl:
                return wl
        return _ensure_default_watchlist()

    if request.method == "POST":
        if request.POST.get("action") == "watchlist_create":
            create_form = WatchlistCreateForm(request.POST)
            if create_form.is_valid():
                name = (create_form.cleaned_data.get("name") or "").strip()
                industry_label = (create_form.cleaned_data.get("industry_label") or "").strip()
                if not name:
                    messages.error(request, "Watchlist name is required.")
                else:
                    wl = Watchlist.objects.create(
                        user=request.user, name=name, industry_label=industry_label
                    )
                    messages.success(request, f"Created watchlist “{wl.name}”.")
                    return redirect(f"{reverse('simulator:watchlist')}?watchlist_id={wl.id}")
            else:
                messages.error(request, "Could not create watchlist.")
            return redirect("simulator:watchlist")

        if request.POST.get("action") == "watchlist_delete":
            delete_form = WatchlistDeleteForm(request.POST)
            if delete_form.is_valid():
                wid = delete_form.cleaned_data["watchlist_id"]
                wl = Watchlist.objects.filter(id=wid, user=request.user).first()
                if not wl:
                    messages.error(request, "Watchlist not found.")
                    return redirect("simulator:watchlist")
                remaining = Watchlist.objects.filter(user=request.user).exclude(id=wl.id).count()
                if remaining <= 0:
                    messages.error(request, "You must have at least one watchlist.")
                    return redirect(f"{reverse('simulator:watchlist')}?watchlist_id={wl.id}")
                wl.delete()
                messages.success(request, "Watchlist deleted.")
            else:
                messages.error(request, "Could not delete watchlist.")
            return redirect("simulator:watchlist")

        if request.POST.get("action") == "watchlist_add":
            active_watchlist = _get_active_watchlist()
            form = WatchlistAddForm(request.POST)
            if form.is_valid():
                instrument = get_or_create_instrument_by_symbol(form.cleaned_data["symbol"])
                WatchlistItem.objects.get_or_create(watchlist=active_watchlist, instrument=instrument)
                fetch_and_store_latest_quote(instrument=instrument)
                messages.success(request, f"Added {instrument.symbol} to watchlist “{active_watchlist.name}”.")
            else:
                messages.error(request, "Could not add to watchlist.")
            return redirect(f"{reverse('simulator:watchlist')}?watchlist_id={active_watchlist.id}")

        if request.POST.get("action") == "watchlist_refresh":
            active_watchlist = _get_active_watchlist()
            items = list(
                WatchlistItem.objects.filter(watchlist=active_watchlist).select_related("instrument")
            )
            refreshed = 0
            failed = 0
            for item in items:
                q = fetch_and_store_latest_quote(instrument=item.instrument)
                if q is None:
                    failed += 1
                else:
                    refreshed += 1
            if refreshed:
                messages.success(request, f"Refreshed {refreshed} quote(s) for “{active_watchlist.name}”.")
            if failed:
                messages.warning(request, f"Failed to refresh {failed} symbol(s). Try again soon.")
            return redirect(f"{reverse('simulator:watchlist')}?watchlist_id={active_watchlist.id}")

        if request.POST.get("action") == "watchlist_remove":
            active_watchlist = _get_active_watchlist()
            form = WatchlistRemoveForm(request.POST)
            if form.is_valid():
                WatchlistItem.objects.filter(
                    watchlist=active_watchlist, instrument_id=form.cleaned_data["instrument_id"]
                ).delete()
                messages.success(request, "Removed from watchlist.")
            else:
                messages.error(request, "Could not remove from watchlist.")
            return redirect(f"{reverse('simulator:watchlist')}?watchlist_id={active_watchlist.id}")

    active_watchlist = _get_active_watchlist()
    all_watchlists = list(Watchlist.objects.filter(user=request.user).order_by("name", "id"))

    add_form = WatchlistAddForm(initial={"watchlist_id": active_watchlist.id})
    create_form = WatchlistCreateForm()
    delete_form = WatchlistDeleteForm(initial={"watchlist_id": active_watchlist.id})

    items = list(
        WatchlistItem.objects.filter(watchlist=active_watchlist)
        .select_related("instrument")
        .order_by("instrument__symbol")
    )
    instrument_ids = [i.instrument_id for i in items]
    latest_prices = {}
    if instrument_ids:
        latest_quotes = (
            Quote.objects.filter(instrument_id__in=instrument_ids)
            .order_by("instrument_id", "-as_of")
            .distinct("instrument_id")
            .only(
                "instrument_id",
                "as_of",
                "price",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "change",
                "percent_change",
                "fifty_two_week_high",
                "fifty_two_week_low",
            )
        )
        latest_prices = {q.instrument_id: q for q in latest_quotes}

    rows = []
    for item in items:
        q = latest_prices.get(item.instrument_id)
        rows.append(
            {
                "instrument_id": item.instrument_id,
                "symbol": item.instrument.symbol,
                "last_price": q.price if q else None,
                "quote_as_of": q.as_of if q else None,
                "open": q.open if q else None,
                "high": q.high if q else None,
                "low": q.low if q else None,
                "change": q.change if q else None,
                "percent_change": q.percent_change if q else None,
                "fifty_two_week_high": q.fifty_two_week_high if q else None,
                "fifty_two_week_low": q.fifty_two_week_low if q else None,
            }
        )

    return render(
        request,
        "simulator/watchlist.html",
        {
            "all_watchlists": all_watchlists,
            "active_watchlist": active_watchlist,
            "create_form": create_form,
            "delete_form": delete_form,
            "add_form": add_form,
            "rows": rows,
            "watchlist_symbols": [r["symbol"] for r in rows],
        },
    )


@login_required
def baskets(request):
    """
    Basket list + create.
    A Basket is a user-owned named group of symbols for future bulk trading.
    """
    if request.method == "POST":
        if request.POST.get("action") == "basket_create":
            form = BasketCreateForm(request.POST)
            if form.is_valid():
                name = (form.cleaned_data.get("name") or "").strip()
                category = (form.cleaned_data.get("category") or "").strip()
                notes = (form.cleaned_data.get("notes") or "").strip()
                if not name:
                    messages.error(request, "Basket name is required.")
                else:
                    b = Basket.objects.create(
                        user=request.user, name=name, category=category, notes=notes
                    )
                    messages.success(request, f"Created basket “{b.name}”.")
                    return redirect("simulator:basket_detail", basket_id=b.id)
            else:
                messages.error(request, "Could not create basket.")
            return redirect("simulator:baskets")

    baskets_list = list(Basket.objects.filter(user=request.user).order_by("-updated_at", "name", "id"))
    create_form = BasketCreateForm()
    return render(
        request,
        "simulator/baskets/list.html",
        {"baskets": baskets_list, "create_form": create_form},
    )


@login_required
def basket_detail(request, basket_id: int):
    basket = Basket.objects.filter(id=basket_id, user=request.user).first()
    if not basket:
        messages.error(request, "Basket not found.")
        return redirect("simulator:baskets")

    if request.method == "POST":
        if request.POST.get("action") == "basket_add_symbol":
            add_form = BasketAddSymbolForm(request.POST)
            if add_form.is_valid():
                if int(add_form.cleaned_data["basket_id"]) != int(basket.id):
                    messages.error(request, "Basket mismatch.")
                    return redirect("simulator:basket_detail", basket_id=basket.id)
                instrument = get_or_create_instrument_by_symbol(add_form.cleaned_data["symbol"])
                BasketItem.objects.get_or_create(basket=basket, instrument=instrument)
                fetch_and_store_latest_quote(instrument=instrument)
                messages.success(request, f"Added {instrument.symbol} to basket “{basket.name}”.")
            else:
                messages.error(request, "Could not add symbol to basket.")
            return redirect("simulator:basket_detail", basket_id=basket.id)

        if request.POST.get("action") == "basket_remove_symbol":
            rm_form = BasketRemoveSymbolForm(request.POST)
            if rm_form.is_valid():
                if int(rm_form.cleaned_data["basket_id"]) != int(basket.id):
                    messages.error(request, "Basket mismatch.")
                    return redirect("simulator:basket_detail", basket_id=basket.id)
                BasketItem.objects.filter(
                    basket=basket, instrument_id=rm_form.cleaned_data["instrument_id"]
                ).delete()
                messages.success(request, "Removed symbol from basket.")
            else:
                messages.error(request, "Could not remove symbol from basket.")
            return redirect("simulator:basket_detail", basket_id=basket.id)

    items = list(
        BasketItem.objects.filter(basket=basket)
        .select_related("instrument")
        .order_by("instrument__symbol", "id")
    )

    add_form = BasketAddSymbolForm(initial={"basket_id": basket.id})
    delete_form = BasketDeleteForm(initial={"basket_id": basket.id})
    return render(
        request,
        "simulator/baskets/detail.html",
        {"basket": basket, "items": items, "add_form": add_form, "delete_form": delete_form},
    )


@login_required
def basket_edit(request, basket_id: int):
    basket = Basket.objects.filter(id=basket_id, user=request.user).first()
    if not basket:
        messages.error(request, "Basket not found.")
        return redirect("simulator:baskets")

    if request.method == "POST":
        form = BasketEditForm(request.POST)
        if form.is_valid() and int(form.cleaned_data["basket_id"]) == int(basket.id):
            name = (form.cleaned_data.get("name") or "").strip()
            category = (form.cleaned_data.get("category") or "").strip()
            notes = (form.cleaned_data.get("notes") or "").strip()
            if not name:
                messages.error(request, "Basket name is required.")
            else:
                basket.name = name
                basket.category = category
                basket.notes = notes
                basket.save(update_fields=["name", "category", "notes", "updated_at"])
                messages.success(request, "Basket updated.")
                return redirect("simulator:basket_detail", basket_id=basket.id)
        else:
            messages.error(request, "Could not update basket.")
        return redirect("simulator:basket_edit", basket_id=basket.id)

    form = BasketEditForm(
        initial={
            "basket_id": basket.id,
            "name": basket.name,
            "category": basket.category,
            "notes": basket.notes,
        }
    )
    return render(request, "simulator/baskets/edit.html", {"basket": basket, "form": form})


@login_required
def basket_delete(request, basket_id: int):
    basket = Basket.objects.filter(id=basket_id, user=request.user).first()
    if not basket:
        messages.error(request, "Basket not found.")
        return redirect("simulator:baskets")

    if request.method == "POST":
        form = BasketDeleteForm(request.POST)
        if form.is_valid() and int(form.cleaned_data["basket_id"]) == int(basket.id):
            name = basket.name
            basket.delete()
            messages.success(request, f"Deleted basket “{name}”.")
            return redirect("simulator:baskets")
        messages.error(request, "Could not delete basket.")
        return redirect("simulator:basket_detail", basket_id=basket.id)

    form = BasketDeleteForm(initial={"basket_id": basket.id})
    return render(request, "simulator/baskets/delete.html", {"basket": basket, "form": form})


@login_required
def watchlist_timeseries(request):
    """
    Return Twelve Data time_series OHLCV for a symbol in the user's watchlist.
    Used by the watchlist chart + history table.
    """
    try:
        watchlist_id = int(request.GET.get("watchlist_id") or 0)
    except (TypeError, ValueError):
        watchlist_id = 0

    raw_symbol = request.GET.get("symbol") or ""
    try:
        symbol = normalize_symbol(raw_symbol)
    except ValueError:
        return JsonResponse({"ok": False, "error": "INVALID_SYMBOL"}, status=400)

    # Security: only allow symbols on the selected watchlist (and owned by this user)
    if not watchlist_id:
        return JsonResponse({"ok": False, "error": "WATCHLIST_REQUIRED"}, status=400)
    if not Watchlist.objects.filter(id=watchlist_id, user=request.user).exists():
        return JsonResponse({"ok": False, "error": "WATCHLIST_NOT_FOUND"}, status=404)
    if not WatchlistItem.objects.filter(
        watchlist_id=watchlist_id, instrument__symbol=symbol
    ).exists():
        return JsonResponse({"ok": False, "error": "NOT_IN_WATCHLIST"}, status=403)

    interval = (request.GET.get("interval") or "1day").strip()
    # Keep MVP simple: allow a small, safe set of intervals
    allowed_intervals = {"1day", "1week", "1month", "1h"}
    if interval not in allowed_intervals:
        interval = "1day"

    try:
        outputsize = int(request.GET.get("outputsize") or 90)
    except (TypeError, ValueError):
        outputsize = 90
    outputsize = max(10, min(outputsize, 365))

    cache_key = f"watchlist_ts:{request.user.id}:{watchlist_id}:{symbol}:{interval}:{outputsize}"
    cached = cache.get(cache_key)
    if cached:
        return JsonResponse(cached)

    try:
        provider = TwelveDataProvider()
        data = provider.fetch_time_series(symbol=symbol, interval=interval, outputsize=outputsize) or {}
    except Exception:
        return JsonResponse({"ok": False, "error": "PROVIDER_ERROR"}, status=502)

    values = data.get("values") if isinstance(data, dict) else None
    if not isinstance(values, list):
        return JsonResponse({"ok": False, "error": "NO_DATA"}, status=404)

    # TwelveData returns most-recent-first; normalize ascending by datetime for chart/table.
    out = []
    for v in values:
        if not isinstance(v, dict):
            continue
        dt = v.get("datetime")
        if not dt:
            continue
        out.append(
            {
                "datetime": dt,
                "open": v.get("open"),
                "high": v.get("high"),
                "low": v.get("low"),
                "close": v.get("close"),
                "volume": v.get("volume"),
            }
        )
    out.reverse()

    payload = {
        "ok": True,
        "symbol": symbol,
        "interval": interval,
        "values": out,
    }
    # Cache briefly to protect the free-tier API budget.
    cache.set(cache_key, payload, timeout=120)
    return JsonResponse(payload)


def competition_metrics_ohlc(request, competition_id: int):
    """
    Dashboard chart data for a participant in a competition.

    Returns OHLC buckets for the selected metric, grouped by day or hour.
    Query params:
      - metric: total_value|cash_balance|holdings_value|unrealized_pnl|realized_pnl_total|realized_pnl_today
      - bucket: day|hour
      - days: int (default 30)
    """
    if not request.user.is_authenticated:
        return JsonResponse({"ok": False, "error": "NOT_AUTHENTICATED"}, status=401)

    participant = (
        CompetitionParticipant.objects.filter(user=request.user, competition_id=competition_id)
        .select_related("competition")
        .first()
    )
    if not participant:
        return JsonResponse({"ok": False, "error": "NOT_JOINED"}, status=403)

    metric = (request.GET.get("metric") or "total_value").strip()
    allowed_metrics = {
        "total_value": "total_value",
        "cash_balance": "cash_balance",
        "holdings_value": "holdings_value",
        "unrealized_pnl": "unrealized_pnl",
        "realized_pnl_total": "realized_pnl_total",
        "realized_pnl_today": "realized_pnl_today",
    }
    if metric not in allowed_metrics:
        metric = "total_value"

    bucket = (request.GET.get("bucket") or "day").strip()
    if bucket not in {"day", "hour"}:
        bucket = "day"

    try:
        days = int(request.GET.get("days") or 30)
    except (TypeError, ValueError):
        days = 30
    days = max(1, min(days, 180))

    since = max(
        participant.competition.week_start_at,
        timezone.now() - timedelta(days=days),
    )

    snaps = list(
        PortfolioSnapshot.objects.filter(participant=participant, as_of__gte=since)
        .only("as_of", allowed_metrics[metric])
        .order_by("as_of")
    )

    def _bucket_key(dt_local):
        if bucket == "hour":
            return dt_local.replace(minute=0, second=0, microsecond=0)
        return dt_local.date()

    buckets = {}
    for s in snaps:
        dt_local = timezone.localtime(s.as_of)
        key = _bucket_key(dt_local)
        val = getattr(s, allowed_metrics[metric])
        if val is None:
            continue
        v = float(val)
        if key not in buckets:
            buckets[key] = {"t": dt_local, "open": v, "high": v, "low": v, "close": v}
        else:
            b = buckets[key]
            b["high"] = max(b["high"], v)
            b["low"] = min(b["low"], v)
            b["close"] = v

    # sort keys chronologically
    items = []
    for key, b in buckets.items():
        if bucket == "hour":
            x = b["t"].isoformat()
        else:
            x = str(key)
        items.append({"x": x, "o": b["open"], "h": b["high"], "l": b["low"], "c": b["close"]})
    if bucket == "hour":
        items.sort(key=lambda r: r["x"])
    else:
        items.sort(key=lambda r: r["x"])

    # If the snapshot table is still blank (e.g., first day before the cron job runs and before
    # any trades are made), return a single "today" bucket based on current computed values so
    # the chart is not empty.
    if not items:
        try:
            now = timezone.now()

            # Compute holdings/unrealized from current positions + latest quotes.
            positions = list(
                participant.positions.filter(quantity__gt=0).values(
                    "instrument_id", "quantity", "avg_cost_basis"
                )
            )
            latest_prices: dict[int, Decimal] = {}
            for pos in positions:
                q = (
                    Quote.objects.filter(instrument_id=pos["instrument_id"])
                    .order_by("-as_of")
                    .only("price")
                    .first()
                )
                if q and q.price is not None:
                    latest_prices[pos["instrument_id"]] = q.price

            holdings_value = Decimal("0.00")
            unrealized = Decimal("0.00")
            for pos in positions:
                price = latest_prices.get(pos["instrument_id"])
                if price is None:
                    continue
                qty = Decimal(pos["quantity"])
                avg_cost = pos["avg_cost_basis"]
                holdings_value += price * qty
                unrealized += (price - avg_cost) * qty

            cash_balance = participant.cash_balance
            total_value = cash_balance + holdings_value

            # Realized P&L totals (same approach as dashboard view)
            realized_total = (
                TradeFill.objects.filter(order__participant=participant, order__side=OrderSide.SELL)
                .aggregate(total=Sum("realized_pnl"))["total"]
                or Decimal("0.00")
            )
            local_now = timezone.localtime(now)
            start_of_day_utc = local_now.replace(
                hour=0, minute=0, second=0, microsecond=0
            ).astimezone(py_timezone.utc)
            realized_today = (
                TradeFill.objects.filter(
                    order__participant=participant,
                    order__side=OrderSide.SELL,
                    filled_at__gte=start_of_day_utc,
                ).aggregate(total=Sum("realized_pnl"))["total"]
                or Decimal("0.00")
            )

            current_val_by_metric: dict[str, Decimal] = {
                "total_value": total_value,
                "cash_balance": cash_balance,
                "holdings_value": holdings_value,
                "unrealized_pnl": unrealized,
                "realized_pnl_total": realized_total,
                "realized_pnl_today": realized_today,
            }
            val = current_val_by_metric.get(metric)
            if val is not None:
                dt_local = timezone.localtime(now)
                key = _bucket_key(dt_local)
                x = dt_local.isoformat() if bucket == "hour" else str(key)
                v = float(val)
                # Add a tiny range so a single-point candle is visible.
                eps = max(0.01, abs(v) * 0.0005)
                items = [{"x": x, "o": v, "h": v + eps, "l": v - eps, "c": v}]
        except Exception:
            # If we can't compute a fallback, just return the empty set.
            pass

    return JsonResponse(
        {
            "ok": True,
            "metric": metric,
            "bucket": bucket,
            "points": items,
        }
    )
