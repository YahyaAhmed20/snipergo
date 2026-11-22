from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.http import HttpResponse
from accounts import views

urlpatterns = [
    path("", views.home, name="home"),
    path("healthz/", lambda r: HttpResponse("ok", content_type="text/plain")),
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),

    path(
        "accounts/3rdparty/signup/",
        __import__("accounts.social_views", fromlist=["SniperSocialSignupView"])
        .SniperSocialSignupView.as_view(),
        name="socialaccount_signup",
    ),
    path(
        "accounts/3rdparty/login/cancelled/",
        views.social_login_cancelled,
        name="socialaccount_login_cancelled",
    ),
    path(
        "accounts/3rdparty/login/error/",
        views.social_login_error,
        name="socialaccount_login_error",
    ),

    path("accounts/", include("allauth.urls")),

    path("api/trips/", include("trips.api_urls")),
    path("vehicles/", include("vehicles.urls")),

]

if settings.DEBUG:
    urlpatterns += static(
        settings.STATIC_URL,
        document_root=settings.STATIC_ROOT,
    )
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT,
    )