
from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path(
        "register/customer/",
        views.register_customer,
        name="register_customer",
    ),
    path(
        "register/captain/",
        views.register_captain,
        name="register_captain",
    ),
    path("login/", views.login_view, name="login"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("logout/", views.logout_view, name="logout"),
    path(
    "captain/status/",
    views.set_operational_status,
    name="set_operational_status",
),
    path(
    "captain/documents/",
    views.captain_documents,
    name="captain_documents",
),
]
