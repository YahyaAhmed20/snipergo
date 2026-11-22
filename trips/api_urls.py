from django.urls import path

from trips.api_views import (
    AvailableTripsAPIView,
    CaptainMyReviewsAPIView,
    CaptainMyTripsAPIView,
    CaptainOfferAcceptAPIView,
    CaptainOfferCreateAPIView,
    CaptainOfferListAPIView,
    CaptainTripActionAPIView,
    TripCancelAPIView,
    TripCreateAPIView,
    TripDetailAPIView,
    CashCollectionConfirmAPIView,
    CustomerMyTripsAPIView,
    CaptainMyOffersAPIView,
    AddressSearchAPIView,
    TripReviewCreateAPIView,
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
    
    path(
        "customer/my-trips/",
        CustomerMyTripsAPIView.as_view(),
        name="customer_my_trips",
    ),

    path("<int:pk>/", TripDetailAPIView.as_view(), name="detail"),
    path(
        "<int:pk>/actions/<str:action>/",
        CaptainTripActionAPIView.as_view(),
        name="captain_action",
    ),
    path(
        "<int:pk>/cash-collection/confirm/",
        CashCollectionConfirmAPIView.as_view(),
        name="cash_collection_confirm",
    ),
    
    path(
        "my-trips/",
        CaptainMyTripsAPIView.as_view(),
        name="captain_my_trips",
    ),
    path(
        "my-offers/",
        CaptainMyOffersAPIView.as_view(),
        name="captain_my_offers",
    ),
    path(
        "address-search/",
        AddressSearchAPIView.as_view(),
        name="address_search",
    ),
    path(
    "<int:pk>/cancel/",
    TripCancelAPIView.as_view(),
    name="cancel",
),

    path(
        "<int:pk>/review/",
        TripReviewCreateAPIView.as_view(),
        name="review_create",
    ),

    path(
        "my-reviews/",
        CaptainMyReviewsAPIView.as_view(),
        name="captain_my_reviews",
    ),
]