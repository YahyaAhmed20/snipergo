
from django.urls import path

from trips.api_views import CaptainTripActionAPIView, TripCreateAPIView, TripDetailAPIView,AvailableTripsAPIView

app_name = "trips_api"

urlpatterns = [
    path("create/", TripCreateAPIView.as_view(), name="create"),
    path("<int:pk>/", TripDetailAPIView.as_view(), name="detail"),
    path(
    "<int:pk>/actions/<str:action>/",
    CaptainTripActionAPIView.as_view(),
    name="captain_action",
),
    path(
    "available/",
    AvailableTripsAPIView.as_view(),
    name="available",
),
]
