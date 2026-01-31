from django.contrib import admin

from .models import Sponsor


@admin.register(Sponsor)
class SponsorAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "website", "contact_email", "created_at")
    search_fields = ("name", "website", "contact_email")
