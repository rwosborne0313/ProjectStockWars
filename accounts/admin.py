from django.contrib import admin

from .models import InvestorProfile


@admin.register(InvestorProfile)
class InvestorProfileAdmin(admin.ModelAdmin):
    list_display = ("id", "display_name", "user", "age_bracket", "experience_level")
    list_filter = ("age_bracket", "experience_level")
    search_fields = ("display_name", "user__username", "user__email")
