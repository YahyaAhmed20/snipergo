from .api_views import (
    CaptainLocationUpdateAPIView,
    CaptainWalletPaymobTopUpAPIView,
    NotificationListAPIView,
    NotificationUnreadCountAPIView,
    NotificationMarkReadAPIView,
    NotificationMarkAllReadAPIView,
)
from django.urls import path

from allauth.socialaccount import views as socialaccount_views

from .views import request_cash_topup
from . import views
from .paymob_views import paymob_transaction_webhook, paymob_return

app_name = "accounts"

urlpatterns = [
  
    path(
        "register/customer/",
        views.register_customer,
        name="register_customer",
    ),
    path(
        "register/captain/google/",
        views.captain_google_signup,
        name="captain_google_signup",
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
    path(
        "profile/",
        views.customer_profile,
        name="customer_profile",
    ),
    path(
        "my-trips/",
        views.customer_trips,
        name="customer_trips",
    ),
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
    path(
        "captain/wallet/",
        views.captain_wallet,
        name="captain_wallet",
    ),
    path(
        "notifications/page/",
        views.notification_page,
        name="notification_page",
    ),
    path(
        "notifications/",
        NotificationListAPIView.as_view(),
        name="notification_list",
    ),
    path(
        "notifications/unread-count/",
        NotificationUnreadCountAPIView.as_view(),
        name="notification_unread_count",
    ),
    path(
        "notifications/<int:notification_id>/read/",
        NotificationMarkReadAPIView.as_view(),
        name="notification_mark_read",
    ),
    path(
        "notifications/mark-all-read/",
        NotificationMarkAllReadAPIView.as_view(),
        name="notification_mark_all_read",
    ),
]