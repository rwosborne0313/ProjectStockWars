from django.urls import path

from . import views

app_name = "backoffice"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("", views.index, name="index"),
    path("<str:app_label>/<str:model_name>/", views.changelist, name="changelist"),
    path("<str:app_label>/<str:model_name>/add/", views.change_form, name="add"),
    path("<str:app_label>/<str:model_name>/<path:object_id>/change/", views.change_form, name="change"),
    path("<str:app_label>/<str:model_name>/<path:object_id>/delete/", views.delete_view, name="delete"),
]

