from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from marketdata.models import Quote
from simulator.models import OrderSide, Position, TradeFill
from simulator.models import CashLedgerEntry, CashLedgerReason

from .models import Competition, CompetitionParticipant, CompetitionStatus, ParticipantStatus


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


def landing(request):
    return render(request, "competitions/landing.html")


def about(request):
    return render(request, "competitions/about.html")


def contact(request):
    return render(request, "competitions/contact.html")


def shareholders(request):
    return render(request, "competitions/shareholders.html")


def terms(request):
    return render(request, "competitions/terms.html")


def current_competitions(request):
    now = timezone.now()
    competitions = (
        Competition.objects.filter(
            status=CompetitionStatus.PUBLISHED,
            week_end_at__gte=now,
        )
        .select_related("sponsor")
        .order_by("-week_start_at")
    )
    joined_ids = set()
    if request.user.is_authenticated:
        joined_ids = set(
            CompetitionParticipant.objects.filter(user=request.user).values_list(
                "competition_id", flat=True
            )
        )
    return render(
        request,
        "competitions/current_list.html",
        {"competitions": competitions, "joined_ids": joined_ids, "now": now},
    )


def competition_detail(request, competition_id: int):
    competition = get_object_or_404(
        Competition.objects.select_related("sponsor"), pk=competition_id
    )
    join_status = None
    if request.user.is_authenticated:
        join_status = (
            CompetitionParticipant.objects.filter(competition=competition, user=request.user)
            .values_list("status", flat=True)
            .first()
        )
    return render(
        request,
        "competitions/detail.html",
        {"competition": competition, "join_status": join_status},
    )


@login_required
def active_competitions(request):
    now = timezone.now()
    competitions = (
        Competition.objects.filter(
            status=CompetitionStatus.PUBLISHED,
            # "Active competitions" is used as the "open to join" list.
            # Include competitions that haven't ended yet (including upcoming).
            week_end_at__gte=now,
        )
        .select_related("sponsor")
        .order_by("-week_start_at")
    )
    joined_ids = set(
        CompetitionParticipant.objects.filter(user=request.user)
        .values_list("competition_id", flat=True)
    )
    return render(
        request,
        "competitions/active_list.html",
        {"competitions": competitions, "joined_ids": joined_ids},
    )


@login_required
def my_competitions(request):
    now = timezone.now()
    participations = list(
        CompetitionParticipant.objects.filter(user=request.user)
        .select_related("competition", "competition__sponsor")
        .order_by("-joined_at")
    )

    # Compute per-competition ranking summaries (no other investor details exposed).
    competition_ids = [p.competition_id for p in participations]
    if competition_ids:
        all_participants = list(
            CompetitionParticipant.objects.filter(
                competition_id__in=competition_ids,
                status=ParticipantStatus.ACTIVE,
            ).only("id", "competition_id", "cash_balance", "starting_cash")
        )
        participant_ids = [p.id for p in all_participants]

        # Latest prices for all instruments held by any participant across these competitions
        pos_rows = list(
            Position.objects.filter(participant_id__in=participant_ids, quantity__gt=0).values(
                "participant_id", "instrument_id", "quantity"
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

        holdings_by_participant: dict[int, Decimal] = {pid: Decimal("0.00") for pid in participant_ids}
        for r in pos_rows:
            price = latest_prices.get(r["instrument_id"])
            if price is None:
                continue
            holdings_by_participant[r["participant_id"]] = holdings_by_participant.get(
                r["participant_id"], Decimal("0.00")
            ) + (price * Decimal(r["quantity"]))

        cash_by_participant: dict[int, Decimal] = {p.id: p.cash_balance for p in all_participants}
        equity_by_participant: dict[int, Decimal] = {
            pid: cash_by_participant.get(pid, Decimal("0.00")) + holdings_by_participant.get(pid, Decimal("0.00"))
            for pid in participant_ids
        }
        return_by_participant: dict[int, Decimal] = {}
        for p in all_participants:
            eq = equity_by_participant.get(p.id, Decimal("0.00"))
            if p.starting_cash:
                return_by_participant[p.id] = (eq - p.starting_cash) / p.starting_cash
            else:
                return_by_participant[p.id] = Decimal("0.00")

        # Realized P&L totals across participants (SELL fills)
        now_local = timezone.localtime(now)
        start_of_day = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
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
        realized_total_by_participant: dict[int, Decimal] = {
            pid: realized_totals.get(pid, Decimal("0.00")) for pid in participant_ids
        }
        realized_today_by_participant: dict[int, Decimal] = {
            pid: realized_todays.get(pid, Decimal("0.00")) for pid in participant_ids
        }

        # Group participants by competition_id for per-competition ranks.
        participants_by_comp: dict[int, list[int]] = {}
        for p in all_participants:
            participants_by_comp.setdefault(p.competition_id, []).append(p.id)

        for p in participations:
            comp_pids = participants_by_comp.get(p.competition_id, [])
            p.rank_total = len(comp_pids)

            # Attach metric values for this participant (always show)
            p.stat_equity = equity_by_participant.get(p.id, Decimal("0.00"))
            p.stat_return = return_by_participant.get(p.id, Decimal("0.00"))
            p.stat_return_pct = p.stat_return * Decimal("100")
            p.stat_cash = cash_by_participant.get(p.id, getattr(p, "cash_balance", Decimal("0.00")))
            p.stat_holdings = holdings_by_participant.get(p.id, Decimal("0.00"))
            p.stat_realized_today = realized_today_by_participant.get(p.id, Decimal("0.00"))
            p.stat_realized_total = realized_total_by_participant.get(p.id, Decimal("0.00"))

            if p.status != ParticipantStatus.ACTIVE:
                p.rank_equity = None
                p.rank_return = None
                p.rank_cash = None
                p.rank_holdings = None
                p.rank_realized_today = None
                p.rank_realized_total = None
                continue

            equity_vals = {pid: equity_by_participant.get(pid, Decimal("0.00")) for pid in comp_pids}
            return_vals = {pid: return_by_participant.get(pid, Decimal("0.00")) for pid in comp_pids}
            cash_vals = {pid: cash_by_participant.get(pid, Decimal("0.00")) for pid in comp_pids}
            holdings_vals = {pid: holdings_by_participant.get(pid, Decimal("0.00")) for pid in comp_pids}
            realized_today_vals = {
                pid: realized_today_by_participant.get(pid, Decimal("0.00")) for pid in comp_pids
            }
            realized_total_vals = {
                pid: realized_total_by_participant.get(pid, Decimal("0.00")) for pid in comp_pids
            }

            p.rank_equity, _ = _rank_desc(equity_vals, p.id)
            p.rank_return, _ = _rank_desc(return_vals, p.id)
            p.rank_cash, _ = _rank_desc(cash_vals, p.id)
            p.rank_holdings, _ = _rank_desc(holdings_vals, p.id)
            p.rank_realized_today, _ = _rank_desc(realized_today_vals, p.id)
            p.rank_realized_total, _ = _rank_desc(realized_total_vals, p.id)
    else:
        for p in participations:
            p.rank_total = 0
            p.rank_equity = None
            p.rank_return = None
            p.rank_cash = None
            p.rank_holdings = None
            p.rank_realized_today = None
            p.rank_realized_total = None
            p.stat_equity = Decimal("0.00")
            p.stat_return = Decimal("0.00")
            p.stat_return_pct = Decimal("0.00")
            p.stat_cash = getattr(p, "cash_balance", Decimal("0.00"))
            p.stat_holdings = Decimal("0.00")
            p.stat_realized_today = Decimal("0.00")
            p.stat_realized_total = Decimal("0.00")

    return render(
        request,
        "competitions/my_competitions.html",
        {"participations": participations, "now": now},
    )


@login_required
def join_competition(request, competition_id: int):
    competition = get_object_or_404(Competition, pk=competition_id)
    if competition.status != CompetitionStatus.PUBLISHED:
        messages.error(request, "Competition is not open for joining.")
        return redirect("competitions:competition_detail", competition_id=competition.id)

    now = timezone.now()

    # Advanced rule: optionally disallow joining after start. Users can join before start but are queued.
    if (
        competition.competition_type == "ADVANCED"
        and getattr(competition, "disallow_join_after_start", False)
    ):
        if now >= competition.week_start_at:
            messages.error(request, "This competition is no longer open to join after it has started.")
            return redirect("competitions:competition_detail", competition_id=competition.id)
        try:
            CompetitionParticipant.objects.create(
                competition=competition,
                user=request.user,
                status=ParticipantStatus.QUEUED,
                starting_cash=competition.starting_cash,
                cash_balance=Decimal("0.00"),
            )
        except IntegrityError:
            messages.info(request, "You are already in this competition.")
            return redirect("competitions:competition_detail", competition_id=competition.id)

        messages.success(request, "You’re queued for this competition. You’ll be activated at the start time.")
        return redirect("competitions:competition_detail", competition_id=competition.id)

    # Provision participant + starting cash atomically (default behavior)
    try:
        with transaction.atomic():
            participant = CompetitionParticipant.objects.create(
                competition=competition,
                user=request.user,
                starting_cash=competition.starting_cash,
                cash_balance=competition.starting_cash,
            )
            CashLedgerEntry.objects.create(
                participant=participant,
                delta_amount=competition.starting_cash,
                reason=CashLedgerReason.STARTING_CASH,
                reference_type="COMPETITION",
                reference_id=competition.id,
            )
    except IntegrityError:
        messages.info(request, "You already joined this competition.")
        return redirect("simulator:dashboard")

    messages.success(
        request,
        f"Joined competition. Starting cash: ${Decimal(participant.starting_cash):,.2f}",
    )
    return redirect("simulator:dashboard_for_competition", competition_id=competition.id)


@login_required
def withdraw_from_competition(request, competition_id: int):
    """
    Withdraw from a competition queue prior to start time.
    Only QUEUED participants can withdraw; ACTIVE participants are not withdrawn by this endpoint.
    """
    competition = get_object_or_404(Competition, pk=competition_id)
    participant = CompetitionParticipant.objects.filter(
        competition=competition, user=request.user
    ).first()
    if not participant:
        messages.error(request, "You are not in this competition.")
        return redirect("competitions:competition_detail", competition_id=competition.id)
    if participant.status != ParticipantStatus.QUEUED:
        messages.error(request, "Only queued participants can withdraw before the competition starts.")
        return redirect("competitions:competition_detail", competition_id=competition.id)

    now = timezone.now()
    if now >= competition.week_start_at:
        messages.error(request, "You can’t withdraw after the competition has started.")
        return redirect("competitions:competition_detail", competition_id=competition.id)

    participant.delete()
    messages.success(request, "You have been removed from the competition queue.")
    return redirect("competitions:competition_detail", competition_id=competition.id)

