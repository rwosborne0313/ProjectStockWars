from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone

from .forms import SignupForm
from .models import AgeBracket, ExperienceLevel, InvestorProfile


def _age_bracket_from_dob(date_of_birth):
    if not date_of_birth:
        return AgeBracket.AGE_25_34

    today = timezone.now().date()
    age = today.year - date_of_birth.year - (
        (today.month, today.day) < (date_of_birth.month, date_of_birth.day)
    )

    if age <= 24:
        return AgeBracket.AGE_18_24
    if age <= 34:
        return AgeBracket.AGE_25_34
    if age <= 44:
        return AgeBracket.AGE_35_44
    if age <= 54:
        return AgeBracket.AGE_45_54
    if age <= 64:
        return AgeBracket.AGE_55_64
    if age <= 74:
        return AgeBracket.AGE_65_74
    if age <= 84:
        return AgeBracket.AGE_75_84
    if age <= 94:
        return AgeBracket.AGE_85_94
    return AgeBracket.AGE_95_104


def signup(request):
    if request.user.is_authenticated:
        return redirect("simulator:dashboard")

    if request.method == "POST":
        form = SignupForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                user = form.save()
                InvestorProfile.objects.create(
                    user=user,
                    display_name=form.cleaned_data["display_name"],
                    age_bracket=_age_bracket_from_dob(form.cleaned_data["date_of_birth"]),
                    experience_level=ExperienceLevel.BEGINNER,
                    first_name=form.cleaned_data["first_name"],
                    last_name=form.cleaned_data["last_name"],
                    address=form.cleaned_data["address"],
                    address2=form.cleaned_data.get("address2", ""),
                    city=form.cleaned_data["city"],
                    state=form.cleaned_data["state"],
                    zip_code=form.cleaned_data["zip_code"],
                    phone=form.cleaned_data["phone"],
                    date_of_birth=form.cleaned_data["date_of_birth"],
                    ssn=form.cleaned_data["ssn"],
                )
            login(request, user)
            messages.success(request, "Account created.")
            return redirect("competitions:my_competitions")
    else:
        form = SignupForm()

    return render(request, "registration/signup.html", {"form": form})


@login_required
def profile(request):
    profile = InvestorProfile.objects.filter(user=request.user).first()
    return render(request, "accounts/profile.html", {"profile": profile})
