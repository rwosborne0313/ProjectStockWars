from __future__ import annotations

from datetime import date
from decimal import Decimal

from django import forms

from marketdata.services import normalize_symbol

from .models import OrderSide, OrderType
from .models import OrderStatus


class TradeTicketForm(forms.Form):
    side = forms.ChoiceField(choices=OrderSide.choices)
    order_type = forms.ChoiceField(choices=OrderType.choices)
    symbol = forms.CharField(max_length=16)
    quantity = forms.IntegerField(min_value=1)
    limit_price = forms.DecimalField(
        required=False, min_value=Decimal("0.01"), decimal_places=2, max_digits=20
    )

    def __init__(self, *args, participant, **kwargs):
        super().__init__(*args, **kwargs)

        # Bootstrap 5 widget styling
        self.fields["side"].widget.attrs.update({"class": "form-select"})
        self.fields["order_type"].widget.attrs.update({"class": "form-select"})
        self.fields["symbol"].widget.attrs.update(
            {"class": "form-control", "autocapitalize": "characters", "autocomplete": "off"}
        )
        self.fields["quantity"].widget.attrs.update({"class": "form-control", "inputmode": "numeric"})
        self.fields["limit_price"].widget.attrs.update({"class": "form-control", "inputmode": "decimal"})

        # Default UX: disable limit price unless LIMIT is selected (JS also enforces this)
        selected_order_type = None
        if self.is_bound:
            selected_order_type = self.data.get(self.add_prefix("order_type"))
        if not selected_order_type:
            selected_order_type = self.initial.get("order_type")
        if selected_order_type != OrderType.LIMIT:
            self.fields["limit_price"].widget.attrs["disabled"] = "disabled"

    def clean(self):
        cleaned = super().clean()
        raw_symbol = cleaned.get("symbol")
        if raw_symbol:
            try:
                cleaned["symbol"] = normalize_symbol(raw_symbol)
            except ValueError as e:
                self.add_error("symbol", str(e))
        order_type = cleaned.get("order_type")
        limit_price = cleaned.get("limit_price")
        if order_type == OrderType.LIMIT and limit_price is None:
            self.add_error("limit_price", "Limit price is required for limit orders.")
        if order_type == OrderType.MARKET and limit_price is not None:
            self.add_error("limit_price", "Market orders must not include a limit price.")
        return cleaned


class WatchlistAddForm(forms.Form):
    symbol = forms.CharField(max_length=16)

    def __init__(self, *args, participant=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["symbol"].widget.attrs.update(
            {"class": "form-control", "autocapitalize": "characters", "autocomplete": "off"}
        )

    def clean(self):
        cleaned = super().clean()
        raw_symbol = cleaned.get("symbol")
        if raw_symbol:
            try:
                cleaned["symbol"] = normalize_symbol(raw_symbol)
            except ValueError as e:
                self.add_error("symbol", str(e))
        return cleaned


class WatchlistRemoveForm(forms.Form):
    instrument_id = forms.IntegerField(min_value=1)


class OrderSearchForm(forms.Form):
    placed_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
    )
    symbol = forms.CharField(
        required=False,
        max_length=16,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "e.g. QQQ",
                "autocapitalize": "characters",
                "autocomplete": "off",
            }
        ),
    )
    order_type = forms.ChoiceField(
        required=False,
        choices=[("", "Any")] + list(OrderType.choices),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    side = forms.ChoiceField(
        required=False,
        choices=[("", "Any")] + list(OrderSide.choices),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    quantity = forms.IntegerField(
        required=False,
        min_value=1,
        widget=forms.NumberInput(attrs={"class": "form-control", "inputmode": "numeric"}),
    )
    status = forms.ChoiceField(
        required=False,
        choices=[("", "Any")] + list(OrderStatus.choices),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    price = forms.DecimalField(
        required=False,
        min_value=Decimal("0.01"),
        decimal_places=2,
        max_digits=20,
        widget=forms.NumberInput(attrs={"class": "form-control", "inputmode": "decimal"}),
    )

    def clean_symbol(self) -> str:
        raw = (self.cleaned_data.get("symbol") or "").strip()
        if not raw:
            return ""
        return normalize_symbol(raw)

