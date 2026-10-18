
from .api_views import (
    CaptainLocationUpdateAPIView,
    CaptainWalletPaymobTopUpAPIView,
)
from django.urls import path

from .views import request_cash_topup
from . import views
from .paymob_views import paymob_transaction_webhook,paymob_return
app_name = "accounts"

urlpatterns = [
    path(
        "register/customer/",
        views.register_customer,
        name="register_customer",
    ),
    path(
    "payments/paymob/webhook/",
    paymob_transaction_webhook,
    name="paymob_transaction_webhook",
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
    path(
    "captain/wallet/top-up/paymob/",
    CaptainWalletPaymobTopUpAPIView.as_view(),
    name="paymob_topup",
),

    path(
        "captain/location/",
        CaptainLocationUpdateAPIView.as_view(),
        name="captain_location_update",
    ),

    path(
    "payments/paymob/return/",
    paymob_return,
    name="paymob_return",
),
    path(
    "captain/wallet/top-up/cash/",
    request_cash_topup,
    name="request_cash_topup",
),
]
