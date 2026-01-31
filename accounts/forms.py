from __future__ import annotations

import re

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.utils.safestring import mark_safe

from .models import AgeBracket, ExperienceLevel, US_STATES


class SignupForm(UserCreationForm):
    email = forms.EmailField(required=True)
    email_confirm = forms.EmailField(required=True, label="Confirm email")
    first_name = forms.CharField(required=True, max_length=150)
    last_name = forms.CharField(required=True, max_length=150)
    address = forms.CharField(required=True, max_length=255)
    address2 = forms.CharField(required=True, max_length=255, label="Address 2")
    city = forms.CharField(required=True, max_length=120)
    state = forms.ChoiceField(required=True, choices=US_STATES)
    zip_code = forms.CharField(required=True, max_length=10, label="Zip Code")
    phone = forms.CharField(required=True, max_length=32)
    date_of_birth = forms.DateField(required=True, widget=forms.DateInput(attrs={"type": "date"}))
    ssn = forms.CharField(
        required=True,
        max_length=11,
        label="SSN",
        widget=forms.PasswordInput(render_value=False),
    )
    display_name = forms.CharField(max_length=32)
    age_bracket = forms.ChoiceField(choices=AgeBracket.choices)
    experience_level = forms.ChoiceField(choices=ExperienceLevel.choices)
    accept_terms = forms.BooleanField(
        required=True,
        label=mark_safe(
            'I agree to the <a href="/terms#terms-of-service" target="_blank" rel="noopener">Terms and Conditions</a> '
            'and <a href="/terms#privacy-policy" target="_blank" rel="noopener">Privacy Policy</a>.'
        ),
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = (
            "username",
            "email",
            "email_confirm",
            "first_name",
            "last_name",
            "address",
            "address2",
            "city",
            "state",
            "zip_code",
            "phone",
            "date_of_birth",
            "ssn",
            "display_name",
            "age_bracket",
            "experience_level",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Bootstrap 5 widget styling
        self.fields["username"].widget.attrs.update(
            {"class": "form-control", "autocomplete": "username"}
        )
        self.fields["email"].widget.attrs.update({"class": "form-control", "autocomplete": "email"})
        self.fields["email_confirm"].widget.attrs.update({"class": "form-control", "autocomplete": "email"})
        self.fields["first_name"].widget.attrs.update({"class": "form-control", "autocomplete": "given-name"})
        self.fields["last_name"].widget.attrs.update({"class": "form-control", "autocomplete": "family-name"})
        self.fields["address"].widget.attrs.update({"class": "form-control", "autocomplete": "address-line1"})
        self.fields["address2"].widget.attrs.update({"class": "form-control", "autocomplete": "address-line2"})
        self.fields["city"].widget.attrs.update({"class": "form-control", "autocomplete": "address-level2"})
        self.fields["state"].widget.attrs.update({"class": "form-select", "autocomplete": "address-level1"})
        self.fields["zip_code"].widget.attrs.update({"class": "form-control", "inputmode": "numeric", "autocomplete": "postal-code"})
        self.fields["phone"].widget.attrs.update({"class": "form-control", "autocomplete": "tel"})
        self.fields["date_of_birth"].widget.attrs.update({"class": "form-control"})
        self.fields["ssn"].widget.attrs.update({"class": "form-control", "inputmode": "numeric"})
        self.fields["display_name"].widget.attrs.update({"class": "form-control"})
        self.fields["age_bracket"].widget.attrs.update({"class": "form-select"})
        self.fields["experience_level"].widget.attrs.update({"class": "form-select"})
        self.fields["password1"].widget.attrs.update(
            {"class": "form-control", "autocomplete": "new-password"}
        )
        self.fields["password2"].widget.attrs.update(
            {"class": "form-control", "autocomplete": "new-password"}
        )

        self.fields["accept_terms"].widget.attrs.update({"class": "form-check-input"})

    def clean(self):
        cleaned = super().clean()
        email = (cleaned.get("email") or "").strip()
        email_confirm = (cleaned.get("email_confirm") or "").strip()
        if email and email_confirm and email.lower() != email_confirm.lower():
            self.add_error("email_confirm", "Email addresses must match.")

        zip_code = (cleaned.get("zip_code") or "").strip()
        if zip_code and not re.fullmatch(r"^\d{5}(\d{4})?$", zip_code):
            self.add_error("zip_code", "Enter a valid ZIP code (5 digits or ZIP+4).")

        ssn = (cleaned.get("ssn") or "").strip()
        ssn_digits = re.sub(r"\D+", "", ssn)
        if ssn_digits and len(ssn_digits) != 9:
            self.add_error("ssn", "Enter a valid SSN (9 digits).")
        cleaned["ssn"] = ssn_digits

        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data.get("email", "")
        user.first_name = self.cleaned_data.get("first_name", "")
        user.last_name = self.cleaned_data.get("last_name", "")
        if commit:
            user.save()
        return user

