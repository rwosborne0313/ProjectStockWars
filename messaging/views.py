from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import render

from .models import MessageDelivery


@login_required
def inbox(request):
    deliveries_qs = (
        MessageDelivery.objects.filter(user=request.user)
        .select_related("message", "message__created_by")
        .order_by("-sent_at", "-id")
    )
    paginator = Paginator(deliveries_qs, 50)
    page_obj = paginator.get_page(request.GET.get("page") or 1)
    return render(
        request,
        "messaging/inbox.html",
        {
            "page_obj": page_obj,
            "deliveries": page_obj.object_list,
        },
    )

