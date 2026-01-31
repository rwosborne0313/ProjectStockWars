from django.urls import path

from . import views

app_name = "competitions"

urlpatterns = [
    path("", views.landing, name="landing"),
    path("about/", views.about, name="about"),
    path("contact/", views.contact, name="contact"),
    path("shareholders/", views.shareholders, name="shareholders"),
    path("terms/", views.terms, name="terms"),
    path("competitions/current/", views.current_competitions, name="current_competitions"),
    path("competitions/active/", views.active_competitions, name="active_competitions"),
    path("competitions/mine/", views.my_competitions, name="my_competitions"),
    path("competitions/<int:competition_id>/", views.competition_detail, name="competition_detail"),
    path("competitions/<int:competition_id>/join/", views.join_competition, name="join_competition"),
]

