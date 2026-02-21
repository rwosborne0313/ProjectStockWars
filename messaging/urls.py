from django.urls import path

from . import views

app_name = "messaging"

urlpatterns = [
    path("messages/", views.inbox, name="inbox"),
]

