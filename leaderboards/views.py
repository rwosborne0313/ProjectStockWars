from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.db.models import F
from django.shortcuts import render
from django.utils import timezone

from competitions.models import Competition, CompetitionStatus
from leaderboards.models import PortfolioSnapshot

# Create your views here.


@login_required
def leaderboard(request):
    now = timezone.now()
    competition = (
        Competition.objects.filter(
            status=CompetitionStatus.PUBLISHED,
            week_start_at__lte=now,
            week_end_at__gte=now,
        )
        .order_by("-week_start_at")
        .first()
    )
    if not competition:
        return render(request, "leaderboards/leaderboard.html", {"competition": None, "rows": []})

    # Latest snapshot per participant in this competition (Postgres DISTINCT ON)
    latest = (
        PortfolioSnapshot.objects.filter(participant__competition=competition)
        .select_related("participant__user")
        .order_by("participant_id", "-as_of")
        .distinct("participant_id")
    )

    ranked = sorted(latest, key=lambda s: s.total_value, reverse=True)

    rows = []
    user_rank = None
    for idx, snap in enumerate(ranked, start=1):
        if snap.participant.user_id == request.user.id:
            user_rank = idx
        rows.append(
            {
                "rank": idx,
                "display": snap.participant.user.username,
                "total_value": snap.total_value,
                "return_pct": snap.return_pct_since_start,
                "as_of": snap.as_of,
            }
        )

    return render(
        request,
        "leaderboards/leaderboard.html",
        {"competition": competition, "rows": rows[:100], "user_rank": user_rank},
    )
