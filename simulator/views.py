from decimal import Decimal
from datetime import datetime, time, timezone as py_timezone, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from competitions.models import CompetitionParticipant, CompetitionStatus, ParticipantStatus
from leaderboards.models import PortfolioSnapshot
from marketdata.models import Quote, WatchlistItem
from marketdata.providers import TwelveDataProvider
from marketdata.services import fetch_and_store_latest_quote, get_or_create_instrument_by_symbol, normalize_symbol

from .forms import OrderSearchForm, TradeTicketForm, WatchlistAddForm, WatchlistRemoveForm
from .models import Order, OrderSide, OrderType, TradeFill
from .services import execute_order


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
                instrument = get_or_create_instrument_by_symbol(watchlist_add_form.cleaned_data["symbol"])
                WatchlistItem.objects.get_or_create(user=request.user, instrument=instrument)
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
            watchlist_items = list(
                WatchlistItem.objects.filter(user=request.user).select_related("instrument")
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
            watchlist_remove_form = WatchlistRemoveForm(request.POST)
            if watchlist_remove_form.is_valid():
                instrument_id = watchlist_remove_form.cleaned_data["instrument_id"]
                WatchlistItem.objects.filter(user=request.user, instrument_id=instrument_id).delete()
                messages.success(request, "Removed from watchlist.")
            else:
                messages.error(request, "Could not remove from watchlist.")
            return redirect("simulator:dashboard_for_competition", competition_id=competition_id)

        form = TradeTicketForm(request.POST, participant=participant)
        if form.is_valid():
            instrument = get_or_create_instrument_by_symbol(form.cleaned_data["symbol"])
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
                if getattr(result.order, "reject_reason", "") == "POSITION_SIZE_LIMIT_33PCT":
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

    recent_orders = (
        Order.objects.filter(participant=participant)
        .select_related("instrument")
        .prefetch_related("fills")
    )

    order_search_form = OrderSearchForm(request.GET or None)
    if order_search_form.is_valid():
        cd = order_search_form.cleaned_data

        if cd.get("placed_date"):
            tz = timezone.get_current_timezone()
            start_local = datetime.combine(cd["placed_date"], time.min, tzinfo=tz)
            end_local = datetime.combine(cd["placed_date"], time.max, tzinfo=tz)
            start_utc = start_local.astimezone(py_timezone.utc)
            end_utc = end_local.astimezone(py_timezone.utc)
            recent_orders = recent_orders.filter(created_at__gte=start_utc, created_at__lte=end_utc)

        if cd.get("symbol"):
            recent_orders = recent_orders.filter(instrument__symbol__iexact=cd["symbol"])
        if cd.get("order_type"):
            recent_orders = recent_orders.filter(order_type=cd["order_type"])
        if cd.get("side"):
            recent_orders = recent_orders.filter(side=cd["side"])
        if cd.get("quantity"):
            recent_orders = recent_orders.filter(quantity=cd["quantity"])
        if cd.get("status"):
            recent_orders = recent_orders.filter(status=cd["status"])
        if cd.get("price") is not None:
            recent_orders = recent_orders.filter(
                Q(submitted_price=cd["price"]) | Q(limit_price=cd["price"]) | Q(fills__price=cd["price"])
            ).distinct()

        recent_orders = recent_orders.order_by("-created_at")[:200]
    else:
        recent_orders = recent_orders.order_by("-created_at")[:50]

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
    watchlist_items = list(
        WatchlistItem.objects.filter(user=request.user)
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

    watchlist_add_form = WatchlistAddForm(participant=participant)

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

    trade_limit_modal = request.session.pop("trade_limit_modal", None)

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
            "trade_limit_modal": trade_limit_modal,
        },
    )


@login_required
def watchlist(request):
    if request.method == "POST":
        if request.POST.get("action") == "watchlist_add":
            form = WatchlistAddForm(request.POST)
            if form.is_valid():
                instrument = get_or_create_instrument_by_symbol(form.cleaned_data["symbol"])
                WatchlistItem.objects.get_or_create(user=request.user, instrument=instrument)
                fetch_and_store_latest_quote(instrument=instrument)
                messages.success(request, f"Added {instrument.symbol} to watchlist.")
            else:
                messages.error(request, "Could not add to watchlist.")
            return redirect("simulator:watchlist")

        if request.POST.get("action") == "watchlist_refresh":
            items = list(WatchlistItem.objects.filter(user=request.user).select_related("instrument"))
            refreshed = 0
            failed = 0
            for item in items:
                q = fetch_and_store_latest_quote(instrument=item.instrument)
                if q is None:
                    failed += 1
                else:
                    refreshed += 1
            if refreshed:
                messages.success(request, f"Refreshed {refreshed} watchlist quote(s).")
            if failed:
                messages.warning(request, f"Failed to refresh {failed} symbol(s). Try again soon.")
            return redirect("simulator:watchlist")

        if request.POST.get("action") == "watchlist_remove":
            form = WatchlistRemoveForm(request.POST)
            if form.is_valid():
                WatchlistItem.objects.filter(
                    user=request.user, instrument_id=form.cleaned_data["instrument_id"]
                ).delete()
                messages.success(request, "Removed from watchlist.")
            else:
                messages.error(request, "Could not remove from watchlist.")
            return redirect("simulator:watchlist")

    add_form = WatchlistAddForm()

    items = list(
        WatchlistItem.objects.filter(user=request.user)
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
            "add_form": add_form,
            "rows": rows,
            "watchlist_symbols": [r["symbol"] for r in rows],
        },
    )


@login_required
def watchlist_timeseries(request):
    """
    Return Twelve Data time_series OHLCV for a symbol in the user's watchlist.
    Used by the watchlist chart + history table.
    """
    raw_symbol = request.GET.get("symbol") or ""
    try:
        symbol = normalize_symbol(raw_symbol)
    except ValueError:
        return JsonResponse({"ok": False, "error": "INVALID_SYMBOL"}, status=400)

    # Security: only allow symbols already on the user's watchlist
    if not WatchlistItem.objects.filter(user=request.user, instrument__symbol=symbol).exists():
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

    cache_key = f"watchlist_ts:{request.user.id}:{symbol}:{interval}:{outputsize}"
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
