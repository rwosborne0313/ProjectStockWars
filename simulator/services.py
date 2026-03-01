from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from competitions.models import CompetitionParticipant, ParticipantStatus
from competitions.models import CompetitionStatus
from competitions.models import CompetitionType
from marketdata.models import Instrument, Quote
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


@dataclass(frozen=True)
class BasketExecutionLeg:
    instrument_id: int
    symbol: str
    side: str
    quantity: int
    price: Decimal
    notional: Decimal
    order_id: int | None = None


@dataclass(frozen=True)
class BasketExecutionResult:
    ok: bool
    message: str
    legs: list[BasketExecutionLeg]
    meta: dict | None = None


def _quantize_money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def _parse_pct_inputs(*, pct_by_instrument_id: dict[int, Decimal]) -> dict[int, Decimal]:
    """
    Normalize incoming percentages (0-100) into weights (0-1).
    Enforces: each > 0, and total == 1.00 (100%).
    """
    weights: dict[int, Decimal] = {}
    total_pct = Decimal("0.00")
    for iid, pct in pct_by_instrument_id.items():
        if pct is None:
            raise ValueError("Missing percent.")
        if pct <= 0:
            raise ValueError("Percent must be > 0 for each symbol.")
        total_pct += pct
        weights[iid] = (pct / Decimal("100"))
    # exact 100% (allow small rounding wiggle by quantizing)
    if _quantize_money(total_pct) != _quantize_money(Decimal("100.00")):
        raise ValueError("Allocations must total 100%.")
    return weights


def execute_basket_order(
    *,
    participant_id: int,
    basket_name: str,
    side: str,
    total_amount: Decimal,
    pct_by_instrument_id: dict[int, Decimal],
    ignore_competition_window: bool = False,
) -> BasketExecutionResult:
    """
    Execute a basket BUY or SELL as a set of immediate MARKET fills (one per symbol).

    - Refreshes quotes for each basket symbol before pricing.
    - Enforces per-symbol percent allocation rules (no 0%, sum=100%, and <= competition max %).
    - Enforces Advanced max_symbols and existing concentration rules (max % of total equity).
    - Creates standard Order/TradeFill rows per leg (so history works unchanged).
    """
    now = timezone.now()

    if side not in {OrderSide.BUY, OrderSide.SELL}:
        return BasketExecutionResult(ok=False, message="Invalid side.", legs=[], meta={"reason": "INVALID_SIDE"})

    total_amount = _quantize_money(Decimal(total_amount or "0"))
    if total_amount <= 0:
        return BasketExecutionResult(
            ok=False,
            message="Total amount must be > 0.",
            legs=[],
            meta={"reason": "INVALID_TOTAL_AMOUNT"},
        )

    try:
        weights = _parse_pct_inputs(pct_by_instrument_id=pct_by_instrument_id)
    except ValueError as e:
        return BasketExecutionResult(
            ok=False,
            message=str(e),
            legs=[],
            meta={"reason": "INVALID_ALLOCATIONS"},
        )

    instrument_ids = sorted(weights.keys())
    instruments = list(Instrument.objects.filter(id__in=instrument_ids).only("id", "symbol"))
    inst_by_id = {i.id: i for i in instruments}
    missing = [iid for iid in instrument_ids if iid not in inst_by_id]
    if missing:
        return BasketExecutionResult(
            ok=False,
            message="One or more basket symbols are missing instruments.",
            legs=[],
            meta={"reason": "MISSING_INSTRUMENTS", "missing_instrument_ids": missing},
        )

    # Refresh quote for each symbol (requirement).
    quotes_by_iid: dict[int, Quote] = {}
    for iid in instrument_ids:
        inst = inst_by_id[iid]
        q = fetch_and_store_latest_quote(instrument=inst)
        if q is None:
            return BasketExecutionResult(
                ok=False,
                message=f"Could not refresh quote for {inst.symbol}. Please try again.",
                legs=[],
                meta={"reason": "QUOTE_REFRESH_FAILED", "symbol": inst.symbol},
            )
        quotes_by_iid[iid] = q

    with transaction.atomic():
        participant = (
            CompetitionParticipant.objects.select_for_update()
            .select_related("competition")
            .get(pk=participant_id)
        )

        if participant.status != ParticipantStatus.ACTIVE:
            return BasketExecutionResult(
                ok=False,
                message="Participant not active.",
                legs=[],
                meta={"reason": "PARTICIPANT_NOT_ACTIVE"},
            )

        # Competition must be within trading window and published unless explicitly bypassed
        # by an administrator-triggered/manual execution path.
        if (
            participant.competition.status != CompetitionStatus.PUBLISHED
            or (
                not ignore_competition_window
                and not (participant.competition.week_start_at <= now <= participant.competition.week_end_at)
            )
        ):
            return BasketExecutionResult(
                ok=False,
                message="Competition not active.",
                legs=[],
                meta={"reason": "COMPETITION_NOT_ACTIVE"},
            )

        competition = participant.competition
        max_alloc_pct = (
            Decimal(competition.max_single_symbol_pct)
            if competition.competition_type == CompetitionType.ADVANCED
            and competition.max_single_symbol_pct is not None
            else MAX_SINGLE_BUY_PCT
        )
        # Allocation rule is based on the basket's requested % split.
        for iid, w in weights.items():
            pct = w * Decimal("100")
            if pct > (Decimal(max_alloc_pct) * Decimal("100")):
                sym = inst_by_id[iid].symbol
                return BasketExecutionResult(
                    ok=False,
                    message=(
                        f"{sym} allocation exceeds the competition max per-symbol percent "
                        f"({(Decimal(max_alloc_pct) * Decimal('100')):f}%)."
                    ),
                    legs=[],
                    meta={
                        "reason": "ALLOCATION_OVER_MAX_PCT",
                        "symbol": sym,
                        "max_pct": str(max_alloc_pct),
                        "requested_pct": str(pct),
                    },
                )

        if side == OrderSide.BUY and total_amount > _quantize_money(Decimal(participant.cash_balance)):
            over = _quantize_money(total_amount - Decimal(participant.cash_balance))
            return BasketExecutionResult(
                ok=False,
                message="Insufficient cash / buying power.",
                legs=[],
                meta={
                    "reason": "INSUFFICIENT_CASH",
                    "requested": str(total_amount),
                    "available": str(_quantize_money(Decimal(participant.cash_balance))),
                    "over": str(over),
                    "basket_name": basket_name,
                },
            )

        # Lock existing positions for relevant instruments (and create placeholders for BUY).
        positions = {
            p.instrument_id: p
            for p in Position.objects.select_for_update()
            .filter(participant=participant, instrument_id__in=instrument_ids)
            .select_related("instrument")
        }
        if side == OrderSide.BUY:
            for iid in instrument_ids:
                if iid not in positions:
                    try:
                        positions[iid] = Position.objects.create(
                            participant=participant, instrument_id=iid, quantity=0
                        )
                    except IntegrityError:
                        positions[iid] = Position.objects.select_for_update().get(
                            participant=participant, instrument_id=iid
                        )

        # Advanced rule: max number of symbols (hard enforcement on BUY only).
        if side == OrderSide.BUY and competition.competition_type == CompetitionType.ADVANCED and competition.max_symbols:
            current_positions_count = Position.objects.filter(
                participant=participant, quantity__gt=0
            ).count()
            new_symbols = 0
            for iid in instrument_ids:
                existing_qty = int(positions[iid].quantity or 0)
                # we'll compute qty later; conservatively count as "new" if not currently held and allocation > 0
                if existing_qty <= 0:
                    new_symbols += 1
            if current_positions_count + new_symbols > int(competition.max_symbols):
                return BasketExecutionResult(
                    ok=False,
                    message=(
                        f"This competition allows at most {int(competition.max_symbols)} symbols in your portfolio. "
                        "Reduce the number of new symbols in your basket."
                    ),
                    legs=[],
                    meta={"reason": "MAX_SYMBOLS_EXCEEDED"},
                )

        # Compute latest prices for equity concentration rule (portfolio % of total equity).
        holdings_value = Decimal("0.00")
        held_positions = list(
            Position.objects.filter(participant=participant, quantity__gt=0).values(
                "instrument_id", "quantity"
            )
        )
        latest_prices: dict[int, Decimal] = {}
        for p in held_positions:
            iid = p["instrument_id"]
            if iid in quotes_by_iid:
                latest_prices[iid] = quotes_by_iid[iid].price
                continue
            q = (
                Quote.objects.filter(instrument_id=iid)
                .order_by("-as_of")
                .only("price")
                .first()
            )
            if q and q.price is not None:
                latest_prices[iid] = q.price
        for p in held_positions:
            price = latest_prices.get(p["instrument_id"])
            if price is None:
                continue
            holdings_value += price * Decimal(p["quantity"])
        total_equity = Decimal(participant.cash_balance) + holdings_value

        legs: list[BasketExecutionLeg] = []
        # Compute intended share quantities from basket total amount.
        for iid in instrument_ids:
            inst = inst_by_id[iid]
            price = Decimal(quotes_by_iid[iid].price)
            weight = weights[iid]
            target_notional = total_amount * Decimal(weight)
            if price <= 0:
                return BasketExecutionResult(
                    ok=False,
                    message=f"Invalid price for {inst.symbol}.",
                    legs=[],
                    meta={"reason": "INVALID_PRICE", "symbol": inst.symbol},
                )
            shares = int((target_notional / price).to_integral_value(rounding=ROUND_FLOOR))
            if shares < 1:
                return BasketExecutionResult(
                    ok=False,
                    message=f"Allocation too small to trade at least 1 share of {inst.symbol} at ${_quantize_money(price)}.",
                    legs=[],
                    meta={
                        "reason": "ALLOCATION_TOO_SMALL",
                        "symbol": inst.symbol,
                        "price": str(_quantize_money(price)),
                    },
                )
            notional = _quantize_money(price * Decimal(shares))
            legs.append(
                BasketExecutionLeg(
                    instrument_id=iid,
                    symbol=inst.symbol,
                    side=side,
                    quantity=shares,
                    price=price,
                    notional=notional,
                )
            )

        # BUY: enforce cash against computed share notionals (<= total amount by design, but keep safe).
        if side == OrderSide.BUY:
            total_notional = _quantize_money(sum((l.notional for l in legs), Decimal("0.00")))
            if total_notional > Decimal(participant.cash_balance):
                over = _quantize_money(total_notional - Decimal(participant.cash_balance))
                return BasketExecutionResult(
                    ok=False,
                    message="Insufficient cash / buying power.",
                    legs=[],
                    meta={
                        "reason": "INSUFFICIENT_CASH",
                        "requested": str(total_notional),
                        "available": str(_quantize_money(Decimal(participant.cash_balance))),
                        "over": str(over),
                        "basket_name": basket_name,
                    },
                )

        # SELL: enforce available shares per leg.
        if side == OrderSide.SELL:
            for l in legs:
                pos = positions.get(l.instrument_id)
                if not pos or int(pos.quantity or 0) <= 0:
                    return BasketExecutionResult(
                        ok=False,
                        message=f"You do not currently hold shares of {l.symbol}.",
                        legs=[],
                        meta={"reason": "NO_POSITION", "symbol": l.symbol},
                    )
                if int(pos.quantity) < int(l.quantity):
                    return BasketExecutionResult(
                        ok=False,
                        message=f"Insufficient shares of {l.symbol} to sell {l.quantity}.",
                        legs=[],
                        meta={
                            "reason": "INSUFFICIENT_SHARES",
                            "symbol": l.symbol,
                            "available_shares": int(pos.quantity),
                            "requested_shares": int(l.quantity),
                        },
                    )

        # Portfolio concentration rule per symbol (same idea as execute_order).
        if side == OrderSide.BUY and total_equity > 0:
            apply_concentration_rule = True
            max_pct_equity = None
            if competition.competition_type == CompetitionType.ADVANCED:
                if competition.max_symbols and int(competition.max_symbols) < 3:
                    apply_concentration_rule = False
                max_pct_equity = competition.max_single_symbol_pct
            else:
                max_pct_equity = MAX_SINGLE_BUY_PCT
            if apply_concentration_rule and max_pct_equity:
                limit_value = _quantize_money(total_equity * Decimal(max_pct_equity))
                for l in legs:
                    pos = positions.get(l.instrument_id)
                    existing_qty = int(getattr(pos, "quantity", 0) or 0)
                    projected_qty = existing_qty + int(l.quantity)
                    projected_value = _quantize_money(Decimal(l.price) * Decimal(projected_qty))
                    if projected_value > limit_value:
                        over = _quantize_money(projected_value - limit_value)
                        return BasketExecutionResult(
                            ok=False,
                            message=(
                                f"Single stock purchases cannot exceed the competition’s max % of your total equity. "
                                f"{l.symbol} is over the limit by ${over:,.2f}."
                            ),
                            legs=[],
                            meta={
                                "reason": "POSITION_SIZE_LIMIT",
                                "symbol": l.symbol,
                                "over_limit_value": str(over),
                                "max_pct": str(max_pct_equity),
                                "total_equity": str(_quantize_money(total_equity)),
                            },
                        )

        # Execute legs
        executed: list[BasketExecutionLeg] = []
        for l in legs:
            q = quotes_by_iid[l.instrument_id]
            fill_price = Decimal(l.price)
            notional = _quantize_money(fill_price * Decimal(l.quantity))

            order = Order.objects.create(
                participant=participant,
                instrument_id=l.instrument_id,
                side=side,
                order_type=OrderType.MARKET,
                quantity=int(l.quantity),
                limit_price=None,
                status=OrderStatus.FILLED,
                submitted_price=fill_price,
                quote_as_of=q.as_of,
                reject_reason="",
            )

            realized_pnl = Decimal("0.00")
            pos = positions.get(l.instrument_id)
            if pos is None:
                pos = Position.objects.select_for_update().get(
                    participant=participant, instrument_id=l.instrument_id
                )
                positions[l.instrument_id] = pos

            if side == OrderSide.SELL:
                realized_pnl = _quantize_money(
                    (fill_price - Decimal(pos.avg_cost_basis)) * Decimal(l.quantity)
                )

            fill = TradeFill.objects.create(
                order=order,
                filled_at=now,
                price=fill_price,
                quantity=int(l.quantity),
                notional=notional,
                realized_pnl=realized_pnl,
            )

            # Apply position + cash changes (mirrors execute_order)
            if side == OrderSide.BUY:
                old_qty = int(pos.quantity or 0)
                new_qty = old_qty + int(l.quantity)
                if new_qty > 0:
                    old_cost = (Decimal(pos.avg_cost_basis) * Decimal(old_qty)) if old_qty else Decimal("0")
                    new_cost = old_cost + (fill_price * Decimal(l.quantity))
                    pos.avg_cost_basis = (new_cost / Decimal(new_qty)) if new_qty else Decimal("0")
                pos.quantity = new_qty
                pos.save(update_fields=["quantity", "avg_cost_basis", "updated_at"])

                participant.cash_balance = Decimal(participant.cash_balance) - notional
                participant.save(update_fields=["cash_balance", "updated_at"])

                CashLedgerEntry.objects.create(
                    participant=participant,
                    delta_amount=-notional,
                    reason=CashLedgerReason.TRADE_BUY,
                    reference_type="ORDER",
                    reference_id=order.id,
                    memo=f"BASKET:{basket_name}",
                )
            else:
                pos.quantity = int(pos.quantity) - int(l.quantity)
                if int(pos.quantity) == 0:
                    pos.avg_cost_basis = Decimal("0")
                if int(pos.quantity) == 0:
                    pos.delete()
                else:
                    pos.save(update_fields=["quantity", "avg_cost_basis", "updated_at"])

                participant.cash_balance = Decimal(participant.cash_balance) + notional
                participant.save(update_fields=["cash_balance", "updated_at"])

                CashLedgerEntry.objects.create(
                    participant=participant,
                    delta_amount=notional,
                    reason=CashLedgerReason.TRADE_SELL,
                    reference_type="ORDER",
                    reference_id=order.id,
                    memo=f"BASKET:{basket_name}",
                )

            executed.append(
                BasketExecutionLeg(
                    instrument_id=l.instrument_id,
                    symbol=l.symbol,
                    side=l.side,
                    quantity=l.quantity,
                    price=l.price,
                    notional=notional,
                    order_id=order.id,
                )
            )

        return BasketExecutionResult(
            ok=True,
            message=f"Basket order executed: {len(executed)} leg(s).",
            legs=executed,
            meta={"basket_name": basket_name},
        )


def execute_order(
    *,
    participant_id: int,
    instrument_id: int,
    side: str,
    order_type: str,
    quantity: int,
    limit_price: Decimal | None = None,
    queued_order_id: int | None = None,
) -> OrderExecutionResult:
    """
    Execute a MARKET or marketable LIMIT order immediately at the latest cached quote price.
    Non-marketable LIMIT orders are rejected immediately (no OPEN state in MVP).
    """
    now = timezone.now()
    queued_order = None
    if queued_order_id is not None:
        queued_order = Order.objects.filter(id=queued_order_id, participant_id=participant_id).first()

    def _persist_order(
        *,
        participant=None,
        status: str,
        submitted_price: Decimal | None = None,
        quote_as_of=None,
        reject_reason: str = "",
    ) -> Order:
        if queued_order is not None:
            queued_order.participant = participant or queued_order.participant
            queued_order.instrument_id = instrument_id
            queued_order.side = side
            queued_order.order_type = order_type
            queued_order.quantity = quantity
            queued_order.limit_price = limit_price
            queued_order.status = status
            queued_order.submitted_price = submitted_price
            queued_order.quote_as_of = quote_as_of
            queued_order.reject_reason = reject_reason
            queued_order.save(
                update_fields=[
                    "participant",
                    "instrument",
                    "side",
                    "order_type",
                    "quantity",
                    "limit_price",
                    "status",
                    "submitted_price",
                    "quote_as_of",
                    "reject_reason",
                ]
            )
            return queued_order

        create_kwargs = {
            "instrument_id": instrument_id,
            "side": side,
            "order_type": order_type,
            "quantity": quantity,
            "limit_price": limit_price,
            "status": status,
            "submitted_price": submitted_price,
            "quote_as_of": quote_as_of,
            "reject_reason": reject_reason,
        }
        if participant is not None:
            create_kwargs["participant"] = participant
        else:
            create_kwargs["participant_id"] = participant_id
        return Order.objects.create(**create_kwargs)

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
            order = _persist_order(
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
        order = _persist_order(
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
        order = _persist_order(
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
            order = _persist_order(
                status=OrderStatus.REJECTED,
                submitted_price=fill_price,
                quote_as_of=latest_quote.as_of,
                reject_reason="LIMIT_PRICE_REQUIRED",
            )
            return OrderExecutionResult(ok=False, order=order, fill=None, message="Limit price required.")
        if side == OrderSide.BUY and fill_price > limit_price:
            order = _persist_order(
                status=OrderStatus.REJECTED,
                submitted_price=fill_price,
                quote_as_of=latest_quote.as_of,
                reject_reason="LIMIT_NOT_MARKETABLE_AT_LATEST_PRICE",
            )
            return OrderExecutionResult(ok=False, order=order, fill=None, message="Buy limit not marketable.")
        if side == OrderSide.SELL and fill_price < limit_price:
            order = _persist_order(
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
            order = _persist_order(
                participant=participant,
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
            order = _persist_order(
                participant=participant,
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
                    order = _persist_order(
                        participant=participant,
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

                order = _persist_order(
                    participant=participant,
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
                order = _persist_order(
                    participant=participant,
                    status=OrderStatus.REJECTED,
                    submitted_price=fill_price,
                    quote_as_of=latest_quote.as_of,
                    reject_reason="INSUFFICIENT_CASH",
                )
                return OrderExecutionResult(ok=False, order=order, fill=None, message="Insufficient cash.")
        else:
            if position.quantity < quantity:
                order = _persist_order(
                    participant=participant,
                    status=OrderStatus.REJECTED,
                    submitted_price=fill_price,
                    quote_as_of=latest_quote.as_of,
                    reject_reason="INSUFFICIENT_SHARES",
                )
                return OrderExecutionResult(ok=False, order=order, fill=None, message="Insufficient shares.")

        order = _persist_order(
            participant=participant,
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

