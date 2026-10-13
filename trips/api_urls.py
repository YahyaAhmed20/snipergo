from django.urls import path

from trips.api_views import (
    AvailableTripsAPIView,
    CaptainOfferAcceptAPIView,
    CaptainOfferCreateAPIView,
    CaptainOfferListAPIView,
    CaptainTripActionAPIView,
    TripCancelAPIView,
    TripCreateAPIView,
    TripDetailAPIView,
)

app_name = "trips_api"

urlpatterns = [
    path("create/", TripCreateAPIView.as_view(), name="create"),
    path("available/", AvailableTripsAPIView.as_view(), name="available"),

    path(
        "<int:pk>/offers/",
        CaptainOfferCreateAPIView.as_view(),
        name="offer_create",
    ),
    path(
        "<int:pk>/offers/list/",
        CaptainOfferListAPIView.as_view(),
        name="offer_list",
    ),
    path(
        "<int:pk>/offers/<int:offer_id>/accept/",
        CaptainOfferAcceptAPIView.as_view(),
        name="offer_accept",
    ),

    path("<int:pk>/", TripDetailAPIView.as_view(), name="detail"),
    path(
        "<int:pk>/actions/<str:action>/",
        CaptainTripActionAPIView.as_view(),
        name="captain_action",
    ),
    path(
    "<int:pk>/cancel/",
    TripCancelAPIView.as_view(),
    name="cancel",
),
]