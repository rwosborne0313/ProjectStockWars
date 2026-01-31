from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from marketdata.models import Quote
from simulator.models import Position
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
            week_start_at__lte=now,
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
        {"competitions": competitions, "joined_ids": joined_ids},
    )


def competition_detail(request, competition_id: int):
    competition = get_object_or_404(
        Competition.objects.select_related("sponsor"), pk=competition_id
    )
    is_joined = False
    if request.user.is_authenticated:
        is_joined = CompetitionParticipant.objects.filter(
            competition=competition, user=request.user
        ).exists()
    return render(
        request,
        "competitions/detail.html",
        {"competition": competition, "is_joined": is_joined},
    )


@login_required
def active_competitions(request):
    now = timezone.now()
    competitions = (
        Competition.objects.filter(
            status=CompetitionStatus.PUBLISHED,
            week_start_at__lte=now,
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

        # Group participants by competition_id for per-competition ranks.
        participants_by_comp: dict[int, list[int]] = {}
        for p in all_participants:
            participants_by_comp.setdefault(p.competition_id, []).append(p.id)

        for p in participations:
            comp_pids = participants_by_comp.get(p.competition_id, [])
            equity_vals = {pid: equity_by_participant.get(pid, Decimal("0.00")) for pid in comp_pids}
            return_vals = {pid: return_by_participant.get(pid, Decimal("0.00")) for pid in comp_pids}
            rank_equity, total = _rank_desc(equity_vals, p.id)
            rank_return, _ = _rank_desc(return_vals, p.id)
            p.rank_total = total
            p.rank_equity = rank_equity
            p.rank_return = rank_return
    else:
        for p in participations:
            p.rank_total = 0
            p.rank_equity = None
            p.rank_return = None

    return render(
        request,
        "competitions/my_competitions.html",
        {"participations": participations},
    )


@login_required
def join_competition(request, competition_id: int):
    competition = get_object_or_404(Competition, pk=competition_id)
    if competition.status != CompetitionStatus.PUBLISHED:
        messages.error(request, "Competition is not open for joining.")
        return redirect("competitions:competition_detail", competition_id=competition.id)

    # Provision participant + starting cash atomically
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

