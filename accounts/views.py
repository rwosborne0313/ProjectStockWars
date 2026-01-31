from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import redirect, render

from .forms import SignupForm
from .models import InvestorProfile


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
                    age_bracket=form.cleaned_data["age_bracket"],
                    experience_level=form.cleaned_data["experience_level"],
                    first_name=form.cleaned_data["first_name"],
                    last_name=form.cleaned_data["last_name"],
                    address=form.cleaned_data["address"],
                    address2=form.cleaned_data["address2"],
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
