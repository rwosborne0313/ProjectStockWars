from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from django.contrib import admin
from django.contrib.auth import logout
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.views import redirect_to_login
from django.contrib.admin.utils import display_for_field, lookup_field
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.html import format_html


def backoffice_staff_required(view_func: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
    def _wrapped(request: HttpRequest, *args, **kwargs):
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return redirect_to_login(request.get_full_path(), login_url=reverse("backoffice:login"))
        if not user.is_active or not user.is_staff:
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return _wrapped


@dataclass(frozen=True)
class RegistryItem:
    app_label: str
    model_name: str
    model: type
    model_admin: admin.ModelAdmin
    verbose_name: str
    verbose_name_plural: str


def _get_registry_items() -> list[RegistryItem]:
    items: list[RegistryItem] = []
    for model, model_admin in admin.site._registry.items():
        opts = model._meta
        items.append(
            RegistryItem(
                app_label=opts.app_label,
                model_name=opts.model_name,
                model=model,
                model_admin=model_admin,
                verbose_name=str(opts.verbose_name),
                verbose_name_plural=str(opts.verbose_name_plural),
            )
        )
    items.sort(key=lambda x: (x.app_label, x.verbose_name_plural))
    return items


def _get_item_or_404(app_label: str, model_name: str) -> RegistryItem:
    for item in _get_registry_items():
        if item.app_label == app_label and item.model_name == model_name:
            return item
    raise Http404("Model not registered in engineeringadmin.")


def _nav_tree(request: HttpRequest) -> dict[str, list[RegistryItem]]:
    tree: dict[str, list[RegistryItem]] = {}
    for item in _get_registry_items():
        # Only show models the user can view.
        if not item.model_admin.has_view_permission(request):
            continue
        tree.setdefault(item.app_label, []).append(item)
    return tree


def login_view(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated and request.user.is_staff:
        return redirect("backoffice:index")

    form = AuthenticationForm(request, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        # AuthenticationForm already authenticated the user and provides get_user()
        user = form.get_user()
        if not user.is_staff:
            form.add_error(None, "This account does not have administrator access.")
        else:
            from django.contrib.auth import login

            login(request, user)
            nxt = request.GET.get("next") or reverse("backoffice:index")
            return redirect(nxt)

    return render(request, "backoffice/login.html", {"form": form})


@backoffice_staff_required
def logout_view(request: HttpRequest) -> HttpResponse:
    logout(request)
    return redirect("backoffice:login")


@backoffice_staff_required
def index(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "backoffice/index.html",
        {
            "nav_tree": _nav_tree(request),
        },
    )


@backoffice_staff_required
def changelist(request: HttpRequest, app_label: str, model_name: str) -> HttpResponse:
    item = _get_item_or_404(app_label, model_name)
    ma = item.model_admin
    if not ma.has_view_permission(request):
        raise PermissionDenied

    # Handle bulk actions (MVP).
    if request.method == "POST" and request.POST.get("action"):
        action_name = request.POST.get("action")
        selected = request.POST.getlist("_selected_action")
        actions = ma.get_actions(request)
        if action_name in actions and selected:
            func, _name, _desc = actions[action_name]
            qs = ma.get_queryset(request).filter(pk__in=selected)
            func(ma, request, qs)
            return redirect(
                reverse("backoffice:changelist", args=[app_label, model_name])
                + ("?" + request.META.get("QUERY_STRING") if request.META.get("QUERY_STRING") else "")
            )

    cl = ma.get_changelist_instance(request)
    list_display = list(cl.list_display)
    headers = []
    for name in list_display:
        # Rough header labels: use ModelAdmin short_description if available.
        label = name
        if name == "__str__":
            label = str(item.verbose_name)
        else:
            attr = getattr(ma, name, None)
            if hasattr(attr, "short_description"):
                label = str(attr.short_description)
        headers.append({"name": name, "label": label})

    rows = []
    for obj in cl.result_list:
        cells = []
        for idx, name in enumerate(list_display):
            f, attr, value = lookup_field(name, obj, ma)
            if f is not None:
                rendered = display_for_field(value, f, empty_value_display="—")
            else:
                rendered = value if value not in (None, "") else "—"
            # first column links to change view if permitted
            if idx == 0 and ma.has_change_permission(request, obj=obj):
                change_url = reverse(
                    "backoffice:change", args=[app_label, model_name, str(obj.pk)]
                )
                rendered = format_html('<a class="link-primary text-decoration-none" href="{}">{}</a>', change_url, rendered)
            cells.append(rendered)
        rows.append({"obj": obj, "cells": cells})

    actions = ma.get_actions(request)
    action_choices = [
        {"name": name, "label": desc}
        for name, (func, n, desc) in actions.items()
        if desc
    ]

    return render(
        request,
        "backoffice/changelist.html",
        {
            "nav_tree": _nav_tree(request),
            "item": item,
            "cl": cl,
            "headers": headers,
            "rows": rows,
            "action_choices": action_choices,
            "can_add": ma.has_add_permission(request),
        },
    )


@backoffice_staff_required
def change_form(
    request: HttpRequest, app_label: str, model_name: str, object_id: str | None = None
) -> HttpResponse:
    item = _get_item_or_404(app_label, model_name)
    ma = item.model_admin

    obj = None
    if object_id:
        obj = ma.get_queryset(request).filter(pk=object_id).first()
        if not obj:
            raise Http404("Object not found.")
        if not ma.has_change_permission(request, obj=obj):
            raise PermissionDenied
    else:
        if not ma.has_add_permission(request):
            raise PermissionDenied

    ModelForm = ma.get_form(request, obj=obj)
    form = ModelForm(request.POST or None, request.FILES or None, instance=obj)

    inline_formsets = []
    for formset, inline in ma.get_formsets_with_inlines(request, obj):
        fs = formset(request.POST or None, request.FILES or None, instance=obj)
        inline_formsets.append({"formset": fs, "inline": inline})

    if request.method == "POST":
        is_valid = form.is_valid()
        for x in inline_formsets:
            is_valid = x["formset"].is_valid() and is_valid

        if is_valid:
            with transaction.atomic():
                new_obj = form.save(commit=False)
                ma.save_model(request, new_obj, form, change=bool(obj))
                form.save_m2m()
                ma.save_related(
                    request,
                    form,
                    [x["formset"] for x in inline_formsets],
                    change=bool(obj),
                )

            if "_continue" in request.POST:
                return redirect(
                    reverse("backoffice:change", args=[app_label, model_name, str(new_obj.pk)])
                )
            return redirect(reverse("backoffice:changelist", args=[app_label, model_name]))

    return render(
        request,
        "backoffice/change_form.html",
        {
            "nav_tree": _nav_tree(request),
            "item": item,
            "obj": obj,
            "form": form,
            "inline_formsets": inline_formsets,
            "can_delete": bool(obj) and ma.has_delete_permission(request, obj=obj),
        },
    )


@backoffice_staff_required
def delete_view(request: HttpRequest, app_label: str, model_name: str, object_id: str) -> HttpResponse:
    item = _get_item_or_404(app_label, model_name)
    ma = item.model_admin

    obj = ma.get_queryset(request).filter(pk=object_id).first()
    if not obj:
        raise Http404("Object not found.")
    if not ma.has_delete_permission(request, obj=obj):
        raise PermissionDenied

    if request.method == "POST":
        with transaction.atomic():
            obj.delete()
        return redirect(reverse("backoffice:changelist", args=[app_label, model_name]))

    return render(
        request,
        "backoffice/delete_confirm.html",
        {
            "nav_tree": _nav_tree(request),
            "item": item,
            "obj": obj,
        },
    )

