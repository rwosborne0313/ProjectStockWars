from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from competitions.models import CompetitionParticipant, ParticipantStatus
from competitions.models import CompetitionStatus
from competitions.models import CompetitionType
from marketdata.models import Quote
from marketdata.services import fetch_and_store_latest_quote

from .pricing import derive_price_from_source
from .models import (
    CashLedgerEntry,
    CashLedgerReason,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    TradeFill,
)


MONEY_QUANT = Decimal("0.01")
MAX_SINGLE_BUY_PCT = Decimal("0.33")
MAX_SINGLE_BUY_PCT_HINT = Decimal("0.329")
PCT_HINT_DELTA = Decimal("0.001")


@dataclass(frozen=True)
class OrderExecutionResult:
    ok: bool
    order: Order
    fill: TradeFill | None
    message: str
    meta: dict | None = None


def _quantize_money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def execute_order(
    *,
    participant_id: int,
    instrument_id: int,
    side: str,
    order_type: str,
    quantity: int,
    limit_price: Decimal | None = None,
) -> OrderExecutionResult:
    """
    Execute a MARKET or marketable LIMIT order immediately at the latest cached quote price.
    Non-marketable LIMIT orders are rejected immediately (no OPEN state in MVP).
    """
    now = timezone.now()

    # Resolve instrument once (needed for on-demand refresh)
    try:
        from marketdata.models import Instrument

        inst = Instrument.objects.get(id=instrument_id)
    except Instrument.DoesNotExist:
        inst = None

    # MARKET orders: always refresh quote first, then fill at refreshed price.
    if order_type == OrderType.MARKET and inst is not None:
        refreshed = fetch_and_store_latest_quote(instrument=inst)
        if refreshed is None:
            order = Order.objects.create(
                participant_id=participant_id,
                instrument_id=instrument_id,
                side=side,
                order_type=order_type,
                quantity=quantity,
                limit_price=limit_price,
                status=OrderStatus.REJECTED,
                reject_reason="QUOTE_REFRESH_FAILED",
            )
            return OrderExecutionResult(
                ok=False,
                order=order,
                fill=None,
                message="Could not refresh quote for market order. Please try again.",
            )
        latest_quote = refreshed
    else:
        latest_quote = (
            Quote.objects.filter(instrument_id=instrument_id)
            .order_by("-as_of")
            .only("id", "as_of", "price")
            .first()
        )
    if not latest_quote:
        # Attempt on-demand fetch for previously unseen symbols.
        if inst:
            latest_quote = fetch_and_store_latest_quote(instrument=inst)
    if not latest_quote:
        order = Order.objects.create(
            participant_id=participant_id,
            instrument_id=instrument_id,
            side=side,
            order_type=order_type,
            quantity=quantity,
            limit_price=limit_price,
            status=OrderStatus.REJECTED,
            reject_reason="NO_QUOTE_AVAILABLE",
        )
        return OrderExecutionResult(ok=False, order=order, fill=None, message="No quote available.")

    max_age = getattr(settings, "MAX_QUOTE_AGE_SECONDS", 300)
    age_seconds = (now - latest_quote.as_of).total_seconds()
    if age_seconds > max_age:
        # Attempt on-demand refresh.
        if inst:
            refreshed = fetch_and_store_latest_quote(instrument=inst)
            if refreshed:
                latest_quote = refreshed
                age_seconds = (now - latest_quote.as_of).total_seconds()
        order = Order.objects.create(
            participant_id=participant_id,
            instrument_id=instrument_id,
            side=side,
            order_type=order_type,
            quantity=quantity,
            limit_price=limit_price,
            status=OrderStatus.REJECTED,
            submitted_price=latest_quote.price,
            quote_as_of=latest_quote.as_of,
            reject_reason=f"QUOTE_STALE_{int(age_seconds)}s",
        )
        return OrderExecutionResult(
            ok=False,
            order=order,
            fill=None,
            message=f"Quote is stale ({int(age_seconds)}s old). Try again after refresh.",
        )

    fill_price = latest_quote.price

    # Marketability check for LIMIT orders (immediate-fill-or-reject only)
    if order_type == OrderType.LIMIT:
        if limit_price is None:
            order = Order.objects.create(
                participant_id=participant_id,
                instrument_id=instrument_id,
                side=side,
                order_type=order_type,
                quantity=quantity,
                limit_price=None,
                status=OrderStatus.REJECTED,
                submitted_price=fill_price,
                quote_as_of=latest_quote.as_of,
                reject_reason="LIMIT_PRICE_REQUIRED",
            )
            return OrderExecutionResult(ok=False, order=order, fill=None, message="Limit price required.")
        if side == OrderSide.BUY and fill_price > limit_price:
            order = Order.objects.create(
                participant_id=participant_id,
                instrument_id=instrument_id,
                side=side,
                order_type=order_type,
                quantity=quantity,
                limit_price=limit_price,
                status=OrderStatus.REJECTED,
                submitted_price=fill_price,
                quote_as_of=latest_quote.as_of,
                reject_reason="LIMIT_NOT_MARKETABLE_AT_LATEST_PRICE",
            )
            return OrderExecutionResult(ok=False, order=order, fill=None, message="Buy limit not marketable.")
        if side == OrderSide.SELL and fill_price < limit_price:
            order = Order.objects.create(
                participant_id=participant_id,
                instrument_id=instrument_id,
                side=side,
                order_type=order_type,
                quantity=quantity,
                limit_price=limit_price,
                status=OrderStatus.REJECTED,
                submitted_price=fill_price,
                quote_as_of=latest_quote.as_of,
                reject_reason="LIMIT_NOT_MARKETABLE_AT_LATEST_PRICE",
            )
            return OrderExecutionResult(ok=False, order=order, fill=None, message="Sell limit not marketable.")

    notional = _quantize_money(fill_price * Decimal(quantity))

    with transaction.atomic():
        participant = (
            CompetitionParticipant.objects.select_for_update()
            .select_related("competition")
            .get(pk=participant_id)
        )

        if participant.status != ParticipantStatus.ACTIVE:
            order = Order.objects.create(
                participant=participant,
                instrument_id=instrument_id,
                side=side,
                order_type=order_type,
                quantity=quantity,
                limit_price=limit_price,
                status=OrderStatus.REJECTED,
                submitted_price=fill_price,
                quote_as_of=latest_quote.as_of,
                reject_reason="PARTICIPANT_NOT_ACTIVE",
            )
            return OrderExecutionResult(ok=False, order=order, fill=None, message="Participant not active.")

        # Competition must be within trading window and published.
        if (
            participant.competition.status != CompetitionStatus.PUBLISHED
            or not (participant.competition.week_start_at <= now <= participant.competition.week_end_at)
        ):
            order = Order.objects.create(
                participant=participant,
                instrument_id=instrument_id,
                side=side,
                order_type=order_type,
                quantity=quantity,
                limit_price=limit_price,
                status=OrderStatus.REJECTED,
                submitted_price=fill_price,
                quote_as_of=latest_quote.as_of,
                reject_reason="COMPETITION_NOT_ACTIVE",
            )
            return OrderExecutionResult(ok=False, order=order, fill=None, message="Competition not active.")

        competition = participant.competition

        position = None
        try:
            position = Position.objects.select_for_update().get(
                participant=participant, instrument_id=instrument_id
            )
        except Position.DoesNotExist:
            try:
                position = Position.objects.create(
                    participant=participant, instrument_id=instrument_id, quantity=0
                )
            except IntegrityError:
                position = Position.objects.select_for_update().get(
                    participant=participant, instrument_id=instrument_id
                )

        existing_qty = int(position.quantity or 0)

        # Advanced competitions may price MARKET buys at synthetic bid/ask instead of last.
        if (
            competition.competition_type == CompetitionType.ADVANCED
            and side == OrderSide.BUY
            and order_type == OrderType.MARKET
        ):
            fill_price = derive_price_from_source(
                last_price=latest_quote.price,
                price_source=competition.market_buy_price_source,
                synthetic_spread_bps=int(competition.synthetic_spread_bps or 0),
            )
            notional = _quantize_money(fill_price * Decimal(quantity))

        # Validate resources
        if side == OrderSide.BUY:
            # Advanced rule: max number of symbols (hard enforcement on BUY only).
            if competition.competition_type == CompetitionType.ADVANCED and competition.max_symbols:
                positions_count = Position.objects.filter(
                    participant=participant, quantity__gt=0
                ).count()
                if existing_qty <= 0 and positions_count >= int(competition.max_symbols):
                    order = Order.objects.create(
                        participant=participant,
                        instrument_id=instrument_id,
                        side=side,
                        order_type=order_type,
                        quantity=quantity,
                        limit_price=limit_price,
                        status=OrderStatus.REJECTED,
                        submitted_price=fill_price,
                        quote_as_of=latest_quote.as_of,
                        reject_reason="MAX_SYMBOLS_EXCEEDED",
                    )
                    return OrderExecutionResult(
                        ok=False,
                        order=order,
                        fill=None,
                        message=(
                            f"This competition allows at most {int(competition.max_symbols)} symbols in your portfolio. "
                            "Sell an existing position before buying a new symbol."
                        ),
                    )

            # Risk control: a single stock purchase must not exceed 33% of total equity
            # at the time of the trade. Equity is computed as cash + market value of positions
            # using the latest cached quotes (including the just-fetched quote for this symbol).
            holdings_value = Decimal("0.00")
            positions = list(
                Position.objects.filter(participant=participant, quantity__gt=0).values(
                    "instrument_id", "quantity"
                )
            )
            latest_prices: dict[int, Decimal] = {instrument_id: fill_price}
            for p in positions:
                iid = p["instrument_id"]
                if iid in latest_prices:
                    continue
                q = (
                    Quote.objects.filter(instrument_id=iid)
                    .order_by("-as_of")
                    .only("price")
                    .first()
                )
                if q and q.price is not None:
                    latest_prices[iid] = q.price

            for p in positions:
                price = latest_prices.get(p["instrument_id"])
                if price is None:
                    continue
                holdings_value += price * Decimal(p["quantity"])

            total_equity = participant.cash_balance + holdings_value

            # If the user already owns this symbol, enforce the 33% limit against the
            # projected total position value (existing + new), not just the incremental buy.
            projected_qty = existing_qty + int(quantity)
            projected_position_value = _quantize_money(fill_price * Decimal(projected_qty))

            # Advanced competitions can override the max % per symbol, but the rule is disabled if max_symbols < 3.
            apply_concentration_rule = True
            max_pct = None
            if competition.competition_type == CompetitionType.ADVANCED:
                if competition.max_symbols and int(competition.max_symbols) < 3:
                    apply_concentration_rule = False
                max_pct = competition.max_single_symbol_pct
            else:
                max_pct = MAX_SINGLE_BUY_PCT

            if (
                total_equity > 0
                and apply_concentration_rule
                and max_pct
                and projected_position_value > _quantize_money(total_equity * Decimal(max_pct))
            ):
                existing_position_value = _quantize_money(fill_price * Decimal(existing_qty)) if existing_qty else Decimal("0.00")
                limit_value = _quantize_money(total_equity * Decimal(max_pct))
                over = _quantize_money(projected_position_value - limit_value)

                max_pct_hint = (
                    (Decimal(max_pct) - PCT_HINT_DELTA) if Decimal(max_pct) > PCT_HINT_DELTA else Decimal(max_pct)
                )
                max_notional_hint = total_equity * max_pct_hint
                if fill_price and fill_price > 0:
                    max_total_shares_329 = int(
                        (max_notional_hint / fill_price).to_integral_value(rounding=ROUND_FLOOR)
                    )
                else:
                    max_total_shares_329 = 0
                max_total_shares_329 = max(0, max_total_shares_329)
                max_additional_shares_329 = max(0, max_total_shares_329 - existing_qty)
                max_total_value_329 = _quantize_money(fill_price * Decimal(max_total_shares_329)) if fill_price else Decimal("0.00")

                reject_reason = (
                    "POSITION_SIZE_LIMIT_33PCT"
                    if competition.competition_type != CompetitionType.ADVANCED
                    and Decimal(max_pct) == MAX_SINGLE_BUY_PCT
                    else "POSITION_SIZE_LIMIT_MAX_PCT"
                )

                order = Order.objects.create(
                    participant=participant,
                    instrument_id=instrument_id,
                    side=side,
                    order_type=order_type,
                    quantity=quantity,
                    limit_price=limit_price,
                    status=OrderStatus.REJECTED,
                    submitted_price=fill_price,
                    quote_as_of=latest_quote.as_of,
                    reject_reason=reject_reason,
                )
                return OrderExecutionResult(
                    ok=False,
                    order=order,
                    fill=None,
                    message="Single stock purchases cannot exceed the competition’s max % of your total equity. Reduce shares and try again.",
                    meta={
                        "symbol": getattr(inst, "symbol", None),
                        "quote_price": str(_quantize_money(fill_price)),
                        "trade_shares": int(quantity),
                        "trade_value": str(notional),
                        "total_equity": str(_quantize_money(total_equity)),
                        # keep legacy key for existing UI
                        "limit_value": str(limit_value),
                        "limit_33_value": str(limit_value),
                        "existing_shares": int(existing_qty),
                        "existing_value": str(existing_position_value),
                        "projected_shares": int(projected_qty),
                        "projected_value": str(projected_position_value),
                        "over_limit_value": str(over),
                        "max_pct": str(max_pct),
                        "max_pct_hint": str(max_pct_hint),
                        "max_total_shares": int(max_total_shares_329),
                        "max_additional_shares": int(max_additional_shares_329),
                        "max_total_value": str(max_total_value_329),
                    },
                )

            if participant.cash_balance < notional:
                order = Order.objects.create(
                    participant=participant,
                    instrument_id=instrument_id,
                    side=side,
                    order_type=order_type,
                    quantity=quantity,
                    limit_price=limit_price,
                    status=OrderStatus.REJECTED,
                    submitted_price=fill_price,
                    quote_as_of=latest_quote.as_of,
                    reject_reason="INSUFFICIENT_CASH",
                )
                return OrderExecutionResult(ok=False, order=order, fill=None, message="Insufficient cash.")
        else:
            if position.quantity < quantity:
                order = Order.objects.create(
                    participant=participant,
                    instrument_id=instrument_id,
                    side=side,
                    order_type=order_type,
                    quantity=quantity,
                    limit_price=limit_price,
                    status=OrderStatus.REJECTED,
                    submitted_price=fill_price,
                    quote_as_of=latest_quote.as_of,
                    reject_reason="INSUFFICIENT_SHARES",
                )
                return OrderExecutionResult(ok=False, order=order, fill=None, message="Insufficient shares.")

        order = Order.objects.create(
            participant=participant,
            instrument_id=instrument_id,
            side=side,
            order_type=order_type,
            quantity=quantity,
            limit_price=limit_price,
            status=OrderStatus.FILLED,
            submitted_price=fill_price,
            quote_as_of=latest_quote.as_of,
        )

        # Realized P&L is computed only on sells (cash-only, long-only MVP):
        # realized = (sell_price - avg_cost_basis) * qty
        realized_pnl = Decimal("0.00")
        if side == OrderSide.SELL:
            realized_pnl = _quantize_money(
                (fill_price - position.avg_cost_basis) * Decimal(quantity)
            )

        fill = TradeFill.objects.create(
            order=order,
            filled_at=now,
            price=fill_price,
            quantity=quantity,
            notional=notional,
            realized_pnl=realized_pnl,
        )

        # Apply position + cash changes
        if side == OrderSide.BUY:
            old_qty = position.quantity
            new_qty = old_qty + quantity
            if new_qty > 0:
                old_cost = (position.avg_cost_basis * Decimal(old_qty)) if old_qty else Decimal("0")
                new_cost = old_cost + (fill_price * Decimal(quantity))
                position.avg_cost_basis = (new_cost / Decimal(new_qty)) if new_qty else Decimal("0")
            position.quantity = new_qty
            position.save(update_fields=["quantity", "avg_cost_basis", "updated_at"])

            participant.cash_balance = participant.cash_balance - notional
            participant.save(update_fields=["cash_balance", "updated_at"])

            CashLedgerEntry.objects.create(
                participant=participant,
                delta_amount=-notional,
                reason=CashLedgerReason.TRADE_BUY,
                reference_type="ORDER",
                reference_id=order.id,
            )
        else:
            position.quantity = position.quantity - quantity
            if position.quantity == 0:
                position.avg_cost_basis = Decimal("0")
            if position.quantity == 0:
                position.delete()
            else:
                position.save(update_fields=["quantity", "avg_cost_basis", "updated_at"])

            participant.cash_balance = participant.cash_balance + notional
            participant.save(update_fields=["cash_balance", "updated_at"])

            CashLedgerEntry.objects.create(
                participant=participant,
                delta_amount=notional,
                reason=CashLedgerReason.TRADE_SELL,
                reference_type="ORDER",
                reference_id=order.id,
            )

        # Advanced rule: soft enforcement on SELL for minimum symbols.
        # We allow the SELL to proceed but return a warning message if the user is now below the minimum.
        warning_message = None
        if (
            side == OrderSide.SELL
            and competition.competition_type == CompetitionType.ADVANCED
            and competition.min_symbols
        ):
            remaining_symbols = Position.objects.filter(participant=participant, quantity__gt=0).count()
            if remaining_symbols < int(competition.min_symbols):
                warning_message = (
                    f"Warning: this competition requires at least {int(competition.min_symbols)} symbols. "
                    "You may be disqualified if you remain below the minimum."
                )

        # Record a snapshot after every filled trade so the dashboard chart can show intraday movement.
        try:
            from leaderboards.services import create_portfolio_snapshot

            create_portfolio_snapshot(participant=participant, as_of=now)
        except Exception:
            # Snapshot failures must not block trading.
            pass

        return OrderExecutionResult(
            ok=True,
            order=order,
            fill=fill,
            message=(
                f"Filled {side} {quantity} @ {fill_price} (quote as of {latest_quote.as_of})."
                + (f" {warning_message}" if warning_message else "")
            ),
        )

